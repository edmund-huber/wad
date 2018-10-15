#!/usr/bin/env python2
import base64
import fcntl
import hashlib
import inspect
import os.path
import re
import shutil
import sys
import tempfile

# TODO: pepwhatever, pyflakes


class UsageException(Exception):
    pass


def command_help(*args):
    _, command_fn = find_matching_command(args)
    if len(args) > 0 and command_fn is None:
        print 'No topic matching "{}".'.format(' '.join(args))
    if command_fn is None:
        print 'Available commands:'
        for command_prefix, command_fn, command_desc in command_fns:
            if command_desc is None:
                command_desc = '(no description available)'
            print '    {} - {}'.format(' '.join(command_prefix), command_desc)
    elif command_fn.__doc__ is None:
        print 'No help available for "{}".'.format(' '.join(args))
    else:
        print command_fn.__doc__


def goto(reference):
    # The reference must not be broken.
    commit = WadObject.look_up(reference)
    # The repository must be clean. TODO
    #if len(diff()) > 0:
    #    raise UsageException("Can't goto when there are un-committed or stashed changes.")
    # TODO check out the actual files.
    # Store the new head.
    head_fn = os.path.join('.wad', 'head')
    try:
        with open(head_fn, 'w') as f:
            f.write(reference)
    except IOError:
        raise UsageException("Broken repository - {} can't be written to!".format(head_fn))
# TODO: multi-tenancy doesn't work.


# TODO: use metaclasses again to get rid of this duplicate?
class WadObjectTypeRegistry(type):

    registry = []

    def __new__(cls, name, bases, attrs):
        new_cls = type.__new__(cls, name, bases, attrs)
        cls.registry.append(new_cls)
        return new_cls

    @classmethod
    def get(cls):
        return cls.registry


# TODO known WadObject subclasses (see Registry), should be automatically taken care of (e.g. .commit_ref)


class WadObjectRegistry(type):
    registry = []

    def __new__(cls, name, bases, attrs):
        new_cls = type.__new__(cls, name, bases, attrs)
        cls.registry.append(new_cls)
        return new_cls

    # TODO: replace with __iter__
    @classmethod
    def get(cls):
        return cls.registry


class WadObject(object):
    __metaclass__ = WadObjectRegistry

    @classmethod
    def _name(cls):
        return cls.__name__.lower()

    def __init__(self, reference): # TODO see below: ref_or_id
        self._stage_dir_and_flock = None
        if reference is None:
            self._reference = None
            self._set_up_stage()
        else:
            # If the reference is fully qualified, check that we are the right
            # type of object.
            splitted = reference.split('/')
            if len(splitted) == 2:
                if splitted[0] != self._name():
                    raise InternalError() # TODO
                self._reference = splitted[1]
            elif len(splitted) > 2:
                raise InternalError() # TODO
            else:
                self._reference = reference # TODO verbiage: _id, because not a reference

    # TODO: make sure __ne__ properly defined, and so on
    def __eq__(self, other):
        return \
            type(self) == type(other) and \
            self._reference == other._reference

    def __str__(self):
        s = self._name() + '('
        first = True
        for attribute in self._attributes | self._optional_attributes:
            if not first:
                s += ', '
            s += '{}="{}"'.format(attribute, self.get(attribute))
            first = False
        return s + ')'

    @classmethod
    def get_reference_prefix(cls):
        return cls._name() + '/'

    @classmethod
    def iterate_all(cls):
        for entry in os.listdir(cls._all_objects_dir()):
            yield cls(entry)

    @classmethod
    def look_up(cls, obj_or_ref):
        if isinstance(obj_or_ref, WadObject):
            ref = obj_or_ref.get_reference()
        else:
            ref = obj_or_ref
        for registered_cls in WadObjectRegistry.get():
            if registered_cls == WadObject:
                continue
            prefix = registered_cls.get_reference_prefix()
            if ref.startswith(prefix):
                return registered_cls(ref[len(prefix):])
        return None

    def does_exist(self):
        return self._reference is None

    def get_reference(self): # TODO all reference -> ref, all object -> obj, only one head, topics have starting_commit
        if self._reference is None:
            raise Exception('no reference assigned yet, did you store()?') # TODO internalexceptoin
        return type(self).get_reference_prefix() + self._reference

    @classmethod
    def _all_objects_dir(cls):
        return os.path.join('.wad', cls._name())

    def _reference_dir(self):
        return os.path.join(self._all_objects_dir(), self._reference)

    def _set_up_stage(self):
        # If staging is already set up, then there's nothing to do.
        if self._stage_dir_and_flock is not None:
            return
        # Create a unique stage for the changes to this object, starting with a
        # copy of the object.
        all_stages_dir = os.path.join('.wad', 'stage', self._name())
        try:
            os.makedirs(all_stages_dir)
        except OSError:
            pass
        stage_dir = tempfile.mkdtemp(dir=all_stages_dir)
        if self._reference is not None:
            # Copy over the contents: I would like to be using
            # shutil.copytree(), but it doesn't like it if the destination
            # directory already exists.
            for dirpath, dirnames, filenames in os.walk(self._reference_dir()):
                for filename in filenames:
                    path = os.path.join(dirpath, filename)
                    relative_path = unroot_path(path, self._reference_dir())
                    stage_path = os.path.join(stage_dir, relative_path)
                    try:
                        os.makedirs(os.path.dirname(stage_path))
                    except OSError: # TODO os.OSError?
                        # TODO it's silly that makedirs can fail if the directories exist already. replace?
                        pass
                    shutil.copy(path, stage_path)
        # The lock lets 'wad status' know that this staging directory is still
        # in use - on the other hand, if another process acquires this lock,
        # waits a few moments, and the directory is still present, then it is
        # likely that there is a programming error resulting in leaked stages.
        # TODO - every time wad is invoked, check for these leaked stages.
        lock_filename = os.path.join(stage_dir, 'lock')
        flock = open(lock_filename, 'w')
        try:
            fcntl.flock(flock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except IOError:
            raise UnreachableError('{} is locked.'.format(lock_filename)) #  TODO?
        else:
            self._stage_dir_and_flock = (stage_dir, flock)

    # TODO context manager for...?

    def object_dir(self):
        if self._stage_dir_and_flock is not None:
            stage_dir, _ = self._stage_dir_and_flock
            return stage_dir
        else:
            assert self._reference is not None
            return self._reference_dir()

    def get(self, attribute):
        # The object must exist.
        object_dir_path = os.path.join(self.object_dir(), attribute)
        if not os.path.isfile(object_dir_path):
            return None
        # An attribute type might get the value from just the path..
        attribute_type = self.find_matching_attribute_type(attribute)
        value = attribute_type.get_from_path(object_dir_path)
        if value is not None:
            return value
        # .. or it might want to be fed the file contents.
        with open(object_dir_path, 'rb') as f:
            contents = f.read()
        value = attribute_type.get_from_contents(contents)
        if value is not None:
            return value
        return None

    def find_matching_attribute_type(self, attribute):
        # Find the matching AttributeType class.
        attribute_type_cls = None
        # rename 'attributetype' TODO
        for cls in WadObjectTypeRegistry.get():
            if attribute.endswith(cls.get_extension()):
                if attribute_type_cls is not None:
                    raise InternalError('more than one attribute type matches the extension: {}'.format(attribute)) # TODO
                attribute_type_cls = cls
        if attribute_type_cls is None:
            raise Exception('no attribute type matches the extension: {}'.format(attribute)) # TODO InternalException
        return attribute_type_cls

    def set(self, attribute, value=None, source_filename=None):
        # Set up the stage and where we're going to store this object.
        self._set_up_stage()
        object_dir_path = os.path.join(self.object_dir(), attribute)
        try:
            os.makedirs(os.path.dirname(object_dir_path))
        except OSError:
            pass
        # Figure out if, and how, we should be storing this value.
        attribute_type = self.find_matching_attribute_type(attribute)
        value, source_filename = attribute_type.set(value=value, source_filename=source_filename)
        if value is not None:
            with open(object_dir_path, 'w') as f:
                f.write(value)
        elif source_filename is not None:
            # TODO, perf? maybe just put in a symlink now, and when unstaged,
            # symlink replaced with a copy of the file
            shutil.copy(source_filename, object_dir_path)
        else:
            raise InternalError() #TODO

    def store(self):
        # If there aren't staged changes, then there's nothing to do.
        if self._stage_dir_and_flock is None:
            return
        # Get rid of the flock.
        stage_dir, flock = self._stage_dir_and_flock
        flock.close()
        os.remove(os.path.join(stage_dir, 'lock'))
        # Check the attributes to make sure the object is complete.
        attributes = set()
        for dirpath, _, filenames in os.walk(stage_dir):
            for fn in filenames:
                attributes.add(unroot_path(os.path.join(dirpath, fn), stage_dir))
        if not attributes.issuperset(self._attributes):
            raise Exception('{}, {}'.format(attributes, self._attributes)) # TODO internalexceptoin
        superset = self._attributes | self._optional_attributes
        if not attributes.issubset(superset):
            raise Exception('{}, {}'.format(attributes, superset)) # TODO internalexceptoin
        # If this WadObject type is supposed to autogenerate its reference,
        # then generate the reference from the sha1 of everything in the staged
        # directory.
        if self._autogen_reference:
            paths = []
            for dirpath, dirnames, filenames in os.walk(self.object_dir()):
                for fn in filenames:
                    paths.append(os.path.join(dirpath, fn))
            paths.sort()
            _hash = hashlib.sha1()
            for path in paths:
                _hash.update('!path!' + base64.b64encode(unroot_path(path, stage_dir)))
                with open(path) as f:
                    for chunk in f.read(1000000):
                        if chunk == '':
                            break
                        _hash.update('!chunk!' + base64.b64encode(chunk))
            self._reference = _hash.hexdigest()
        assert self._reference is not None
        # Then move the stage directory over to the right place.
        if os.path.exists(self._reference_dir()):
            shutil.rmtree(self._reference_dir())
        shutil.move(stage_dir, self._reference_dir())
        self._stage_dir_and_flock = None


# replace remaining uses of relative_dirpath with this
def unroot_path(path, root):
    dirname = os.path.dirname(path)
    relative_dirname = os.path.relpath(dirname, root)
    if relative_dirname == '.':
        relative_dirname = ''
    unrooted_path = os.path.join(relative_dirname, os.path.basename(path))
    return unrooted_path


class Topic(WadObject):
    __metaclass__ = WadObjectRegistry
    _attributes = {'description.str', 'tip.commit_ref'}
    _optional_attributes = set()
    _autogen_reference = False


def get_head():
    head_fn = os.path.join('.wad', 'head')
    try:
        with open(head_fn) as f:
            (head,) = f.readlines()
    except IOError:
        raise UsageException('Broken repository - {} does not exist!'.format(head_fn))
    obj = WadObject.look_up(head)
    if not isinstance(obj, Topic) and not isinstance(obj, Commit):
        raise InternalError('{} is not a valid head.'.format(obj))
    return obj


def get_head_commit():
    obj = get_head()
    if isinstance(obj, Topic):
        return obj.get('tip.commit_ref')
    elif isinstance(obj, Commit):
        return obj
    else:
        raise UnreachablException() #TODO


def new_topic(name, starting_from_commit=None): # TODO: and 'starting from' argument
    topic = Topic(name)
    if topic.does_exist():
        raise UsageException('Topic "{}" already exists.'.format(name))
    # TODO name must be a-z and underscores
    topic.set('description.str', 'TODO - new topic')
    if starting_from_commit is None:
        topic.set('tip.commit_ref', get_head_commit())
    else:
        topic.set('tip.commit_ref', starting_from_commit)
    topic.store()
    goto(topic.get_reference())


class Entry(WadObject):
    __metaclass__ = WadObjectRegistry
    _attributes = {'name.str', 'permissions.str'}
    _optional_attributes = {'contents.file', 'contents_file_hash.str', 'contents.entry_ref_set'}
    _autogen_reference = True


def calculate_file_hash(path):
    _hash = hashlib.sha1()
    with open(path) as f:
        for chunk in f.read(1000000):
            if chunk == '':
                break
            _hash.update(chunk)
    return _hash.hexdigest()


def register_path(path):
    if os.path.isfile(path) or os.path.isdir(path): # TODO elif link..
        e = Entry(None)
        e.set('name.str', os.path.basename(path))
        e.set('permissions.str', 'TODO')
        if os.path.isfile(path):
            e.set('contents.file', source_filename=path)
            e.set('contents_file_hash.str', calculate_file_hash(path))
        elif os.path.isdir(path):
            entries = set()
            for entry in os.listdir(path):
                sub_path = os.path.join(path, entry)
                if sub_path != './.wad': # TODO need to think about how/whether wad works if called out of current dir
                    _object = register_path(sub_path)
                    entries.add(_object)
            e.set('contents.entry_ref_set', entries)
        e.store()
        return e
    raise UnreachableException() #TODO


def command_init():
    """wad init

    Creates a wad in the current directory.
    """
    try:
        os.mkdir('.wad')
    except OSError:
        raise UsageException('Directory {} is already a wad.'.format(os.path.abspath('.')))
    init_commit = Commit(None)
    init_commit.set('description.str', 'wad init')
    root = register_path('.')
    root.store()
    init_commit.set('root.entry_ref', root)
    init_commit.store()
    new_topic('main', starting_from_commit=init_commit)


def check_is_wad_repository():
    if not os.path.exists('.wad'):
        raise UsageException('Directory {} is not a wad. Try `wad init`.'.format(os.path.abspath('.')))


def command_status():
    """wad status

    Shows the head, changes, etc.
    """
    check_is_wad_repository()
    print 'head: {}'.format(get_head().get_reference())


# TODO all look_up_commit -> WadObject.look_up
def command_log():
    check_is_wad_repository()
    commit = get_head_commit()
    for _ in range(10):
        if not isinstance(commit, Commit):
            raise InternalException() # TODO
        print '{}    {}'.format(commit.get_reference(), commit.get('description.str'))
        parent = commit.get('parent.commit_ref')
        if parent is None:
            break
        commit = WadObject.look_up(parent)


def command_diff():
    root_entry = get_head_commit().get('root.entry_ref')
    if root_entry.get('name.str') != '.':
        raise InternalError() # TODO
    for value in diff_entry('.', root_entry):
        print value



def diff_entry(path, entry):
    if path == './.wad':
        return

    # TODO: if link?
    if not os.path.exists(path):
        path_type = None
    elif os.path.isfile(path):
        path_type = 'f'
    elif os.path.isdir(path):
        path_type = 'd'
    else:
        raise UnreachableException() # TODO

    if entry is None:
        entry_type = None
    elif entry.get('contents.file') is not None:
        entry_type = 'f'
    elif entry.get('contents.entry_ref_set') is not None:
        entry_type = 'd'
    else:
        raise UnreachableException() # TODO

    if path_type == 'd':
        on_disk = set(os.listdir(path))
    else:
        on_disk = set()
    if entry_type == 'd':
        in_repo = {entry.get('name.str'): entry for entry in entry.get('contents.entry_ref_set')}
    else:
        in_repo = {}
    for name in on_disk | set(in_repo.keys()):
        for value in diff_entry(os.path.join(path, name), in_repo.get(name)):
            yield value

    if path_type is None and entry_type is not None:
        yield ('delete', path)
    elif path_type is not None and entry_type is None:
        yield ('insert', path)
    elif path_type != entry_type:
        yield ('replace', path)
    elif path_type == 'f' and entry_type == 'f':
        # check permissions
        # check sha
        # sha differ? do diff
        if calculate_file_hash(path) != entry.get('contents_file_hash.str'):
            yield ('modify', path)
    elif path_type == 'd' and entry_type == 'd':
        # check permissions
        #yield ('check', path)
        pass
    else:
        raise UnreachableException() # TODO


def command_topic():
    check_is_wad_repository()
    head = get_head()
    for topic in Topic.iterate_all():
        print '{} {}'.format(
            '*' if topic == head else ' ',
            topic.get('description.str')
        )


def command_new_topic(reference): # TODO optional: starting_from
    if reference is None:
        raise UsageException('"new topic" needs a reference') # TODO: UsageException
    new_topic(reference)


def command_new_commit(description):
    if description is None:
        raise Exception('"new commit" needs a description') # TODO: UsageException
    # Make a new commit starting from the head.
    head = get_head_commit()
    new_commit = Commit(None)
    new_commit.set('description.str', description)
    root = register_path('.') # TODO should happen automatically on Commit()? (these 3 lines)
    root.store()
    new_commit.set('parent.commit_ref', head)
    new_commit.set('root.entry_ref', root)
    new_commit.store()
    # TODO ^ this is all identical to part of new_topic
    # If the head is a topic, alter it.
    head = get_head()
    if isinstance(head, Topic):
        head.set('tip.commit_ref', new_commit)
        head.store()
        goto(head.get_reference())
    elif isinstance(head, Commit):
        # If we're in detached mode, the new head is just whatever we finished
        # committing.
        goto(new_commit.get_reference())
    else:
        raise UnreachableException()


# TODO: author
# for author, need to read a ~/.wadconfig
class Commit(WadObject):
    __metaclass__ = WadObjectRegistry
    _attributes = {'description.str', 'root.entry_ref'}
    _optional_attributes = {'parent.commit_ref'}
    _autogen_reference = True


class StrType(object):
    __metaclass__ = WadObjectTypeRegistry

    @classmethod
    def get_extension(cls):
        return '.str'

    @classmethod
    def set(cls, value=None, source_filename=None):
        if source_filename is not None:
            raise InternalError() # TODO
        if not isinstance(value, str):
            raise InternalError() # TODO
        # TODO.. check utf8 encoded?
        return value, None

    @classmethod
    def get_from_path(cls, path):
        return None

    @classmethod
    def get_from_contents(cls, contents):
        return contents


class RefType(object):

    @classmethod
    def get_extension(cls):
        return '.' + cls._inner_type.__name__.lower() + '_ref'

    @classmethod
    def set(cls, value=None, source_filename=None):
        if source_filename is not None:
            raise InternalError() # TODO
        if not issubclass(cls._inner_type, WadObject):
            raise InternalError() # TODO
        if not isinstance(value, cls._inner_type):
            raise InternalError() # TODO
        return value.get_reference(), None

    @classmethod
    def get_from_path(cls, path):
        return None

    @classmethod
    def get_from_contents(cls, contents):
        return cls._inner_type(contents)


class CommitRefType(RefType):
    __metaclass__ = WadObjectTypeRegistry
    _inner_type = Commit


class EntryRefType(RefType):
    __metaclass__ = WadObjectTypeRegistry
    _inner_type = Entry


class FileType(object):
    __metaclass__ = WadObjectTypeRegistry

    @classmethod
    def get_extension(cls):
        return '.file'

    @classmethod
    def set(cls, value=None, source_filename=None):
        if value is not None:
            raise InternalError() # TODO
        if source_filename is None:
            raise InternalError() # TODO
        return None, source_filename

    @classmethod
    def get_from_path(cls, path):
        return None
    # TODO instead of returning contents as below, return file object from path here?

    @classmethod
    def get_from_contents(cls, contents):
        return contents


class RefSetType(object):

    @classmethod
    def get_extension(cls):
        return '.' + cls._inner_type.__name__.lower() + '_ref_set'

    @classmethod
    def set(cls, value=None, source_filename=None):
        if source_filename is not None:
            raise InternalError() # TODO
        if not issubclass(cls._inner_type, WadObject):
            raise InternalError() # TODO
        if not isinstance(value, set):
            raise InternalError() # TODO
        if not all(isinstance(v, cls._inner_type) for v in value):
            raise InternalError() # TODO
        sorted_ref_set = [v.get_reference() for v in value]
        sorted_ref_set.sort()
        value = '\n'.join(sorted_ref_set)
        return value, None

    @classmethod
    def get_from_path(cls, path):
        return None

    @classmethod
    def get_from_contents(cls, contents):
        objs = []
        for maybe_ref in contents.split('\n'):
            if maybe_ref != '':
                objs.append(cls._inner_type(maybe_ref))
        return objs


class EntryRefSetType(RefSetType):
    __metaclass__ = WadObjectTypeRegistry
    _inner_type = Entry


def command_goto(reference):
    if reference is None:
        raise Exception('"goto" needs a reference') # TODO: UsageException
    goto(reference)


def command_restack():
    pass # TODO


command_fns = (
    (('help',), command_help, None),
    (('init',), command_init, 'Creates a wad in the current directory'),
    (('status',), command_status, 'Shows the current topic, changes, etc'),
    (('log',), command_log, 'Lists commits from the head backwards'),
    (('topic',), command_topic, 'Lists all topic'),
    (('new', 'topic'), command_new_topic, 'Creates a new topic and goes to it'), # TODO check still working
    (('new', 'commit'), command_new_commit, 'Creates a new commit on top of head using the diff'), # this next
    # TODO: need some way to do staging, i.e., 'commit only these files'
    (('diff',), command_diff, 'Shows the diff'), # this shoudl also work
    (('goto',), command_goto, 'Goes to the given reference'), # after new commit/diff, then this should work, will need to implement checking out of files
    (('restack',), command_restack, 'Change the parent of the given commit to a different commit')
    # TODO: add clean: looks for orphaned objects and abandoned stages
    # TODO: add dump <reference>: loads up the reference and calls a WadObject type -specific .dump()
    # TODO: reflog
)


def find_matching_command(inp):
    command_prefix = None
    command_fn = None
    for prefix, fn, _ in command_fns:
        if tuple(inp[:len(prefix)]) == prefix:
            # TODO: catch all exceptions/asserts and print 'an internal error has occured..'
            # If there's more than one matching command prefix, then we screwed
            # up.
            assert command_prefix is None
            command_prefix = prefix
            command_fn = fn
    return command_prefix, command_fn


# Find the command corresponding to the command line arguments.
command_prefix, command_fn = find_matching_command(sys.argv[1:])

# If we can't find the command that the user asked for, or there's no command,
# then show `wad help`.
if command_prefix is None:
    # And we're not interested in `help` showing the topic related to whatever
    # is on the command line.
    command_prefix = tuple(sys.argv[1:])
    command_fn = command_help

# Call the command with the arguments passed in on the command line.
command_args = sys.argv[1 + len(command_prefix):]
inspect_command_fn = inspect.getargspec(command_fn)
if inspect_command_fn.varargs is not None:
    # A function with 'varargs' can handle any number of parameters.
    pass
elif len(command_args) < len(inspect_command_fn.args):
    # Otherwise, we should substitute None for all the arguments missing from
    # the command line.
    missing_args = len(inspect_command_fn.args) - len(command_args)
    command_args.extend([None] * missing_args)
elif len(command_args) > len(inspect_command_fn.args):
    # If more arguments are passed in than the function accepts, that's an
    # error.
    raise Exception('blah blah')
command_fn(*command_args)

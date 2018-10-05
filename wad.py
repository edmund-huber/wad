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

class WadObjectRegistry(type):

    registry = []

    def __new__(cls, name, bases, attrs):
        new_cls = type.__new__(cls, name, bases, attrs)
        cls.registry.append(new_cls)
        return new_cls

    @classmethod
    def get(cls):
        return cls.registry


class WadObject(object):

    _type = None

    def __init__(self, reference):
        self._stage_dir_and_flock = None
        self._reference = reference # TODO: sub_ref, because not canonical like commit/323342
        if reference is None:
            self._set_up_stage()

    @classmethod
    def look_up(cls, reference):
        for registered_cls in WadObjectRegistry.get():
            prefix = registered_cls._type + '/' # TODO should be one place to build this
            if reference.startswith(prefix):
                return registered_cls(reference[len(prefix):])
        return None

    def does_exist(self):
        return self._reference is None

    def get_reference(self):
        if self._reference is None:
            raise Exception() # TODO internalexceptoin
        return self._type + '/' + self._reference

    def _reference_dir(self):
        return os.path.join('.wad', self._type, self._reference)

    def _set_up_stage(self):
        # If staging is already set up, then there's nothing to do.
        if self._stage_dir_and_flock is not None:
            return
        # Create a unique stage for the changes to this object, starting with a
        # copy of the object.
        all_stages_dir = os.path.join('.wad', 'stage', self._type)
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
                relative_dirpath = os.path.relpath(dirpath, self._reference_dir())
                for dirname in dirnames:
                    os.makedirs(os.path.join(self._reference_dir(), relative_dirpath, dirname))
                for filename in filenames:
                    shutil.copy(
                        os.path.join(dirpath, filename),
                        os.path.join(self._reference_dir(), relative_dirpath, filename)
                    )
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

    def get(self, path):
        fn = os.path.join(self.object_dir(), key)
        if os.path.isfile(fn):
            with open(fn) as f:
                return f.read()
        return None

    def set(self, path, value=None, source_filename=None):
        self._set_up_stage()
        object_dir_path = os.path.join(self.object_dir(), path)
        try:
            os.makedirs(os.path.dirname(object_dir_path))
        except OSError:
            pass
        if value is not None:
            assert source_filename is None
            with open(object_dir_path, 'w') as f:
                f.write(value)
        elif source_filename is not None:
            assert value is None
            shutil.copy(source_filename, object_dir_path)

    def store(self):
        # If there are staged changes:
        if self._stage_dir_and_flock is not None:
            stage_dir, flock = self._stage_dir_and_flock
            # first check that required content isn't missing.
            for path in self._required_contents:
                path = os.path.join(stage_dir, path)
                if not os.path.exists(path):
                    raise Exception('missing path {}'.format(path)) # TODO InternalExceptoin
            # then move the directory over to the right place.
            flock.close()
            os.remove(os.path.join(stage_dir, 'lock'))
            shutil.move(stage_dir, self._reference_dir())


class Topic(WadObject):
    __metaclass__ = WadObjectRegistry

    _type = 'topic'
    _required_contents = {
        'description',
        'head'
    }


def get_head():
    head_fn = os.path.join('.wad', 'head')
    try:
        with open(head_fn) as f:
            (head,) = f.readlines()
            return head
    except IOError:
        raise UsageException('Broken repository - {} does not exist!'.format(head_fn))
    raise UnreachableException()


def new_topic(name, starting_from_commit=None): # TODO: and 'starting from' argument
    topic = Topic(name)
    if topic.does_exist():
        raise UsageException('Topic "{}" already exists.'.format(name))
    # TODO name must be a-z and underscores
    topic.set('description', 'TODO')
    if starting_from_commit is None:
        topic.set('head', look_up_commit(get_head()))
    else:
        topic.set('head', starting_from_commit.get_reference())
    topic.store()
    goto(topic.get_reference())


def command_init():
    """wad init

    Creates a wad in the current directory.
    """
    try:
        os.mkdir('.wad')
    except OSError:
        raise UsageException('Directory {} is already a wad.'.format(os.path.abspath('.')))
    init_commit = Commit(None)
    init_commit.set('description', 'wad init')
    for dirpath, dirnames, filenames in os.walk('.'):
        # TODO need to do .wad/ignore
        if dirpath.startswith(os.path.join('.', '.wad', '')):
            # Don't include files under '.wad/'.
            continue
        for fn in filenames:
            relative_dirpath = os.path.relpath(dirpath, '.')
            init_commit.set(
                os.path.join('tree', relative_dirpath, fn),
                source_filename=os.path.join(dirpath, fn)
            )
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
    print 'head: {}'.format(get_head())


def command_log():
    check_is_wad_repository()
    commit = look_up_commit(get_head())
    for _ in range(10):
        print commit.description
        if commit.parent_reference is None:
            break
        commit = look_up_commit(commit.parent_reference)


def command_diff():
    print 'diff' # TODO


def command_topic():
    check_is_wad_repository()
    head_ref = get_head()
    for topic_fn in glob.glob(os.path.join('.wad', 'T:*')):
        topic_ref = os.path.basename(topic_fn)
        print '{} {}'.format(
            '*' if topic_ref == head_ref else ' ',
            topic_ref
        )


def command_new_topic(reference): # TODO optional: starting_from
    if reference is None:
        raise UsageException('"new topic" needs a reference') # TODO: UsageException
    new_topic(reference)


def command_new_commit(description):
    if description is None:
        raise Exception('"new commit" needs a description') # TODO: UsageException
    head = get_head()
    # pack up all the changes in the commit
    commit = Commit(look_up_commit(head).get_reference(), description)
    # TODO for filename in diff(): commit.add_files(..)
    # do not proceed if nothing to commit
    commit.store()
    # TODO: add a typesregistry for wadobjects, so the scaffolded if isn't necessary
    _object = WadObject.look_up(head)
    if isinstance(_object, Topic):
        topic = _object
        topic.set('head', commit.get_reference())
        topic.store()
        goto(head)
    elif isinstance(_object, Commit):
        # If we're in detached mode, the new head is just whatever we finished
        # committing.
        goto(commit.get_reference())
    else:
        raise UnreachableException()


# TODO: author
# for author, need to read a ~/.wadconfig
class Commit(WadObject):
    __metaclass__ = WadObjectRegistry

    _type = 'commit'
    _required_contents = {
        'description',
        'tree'
    }

    def store(self):
        # If there are staged changes, figure out what the new reference should
        # be.
        if self._stage_dir_and_flock is not None:
            # Find all files in the commit, sort them, then do a sha1 hash of all
            # filenames and their contents.
            paths = []
            for dirpath, dirnames, filenames in os.walk(self.object_dir()):
                for fn in filenames:
                    paths.append(os.path.join(dirpath, fn))
            paths.sort()
            _hash = hashlib.sha1()
            for path in paths:
                _hash.update('!path!' + base64.b64encode(path))
                with open(path) as f:
                    for chunk in f.read(1000000):
                        if chunk == '':
                            break
                        _hash.update('!chunk!' + base64.b64encode(chunk))
            self._reference = _hash.hexdigest()
        # TODO: might need to think about copying metadata too -- so that mtime is preserved?
        super(Commit, self).store()


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
    (('new', 'topic'), command_new_topic, 'Creates a new topic and goes to it'),
    (('new', 'commit'), command_new_commit, 'Creates a new commit on top of head using the diff'),
    # TODO: need some way to do staging, i.e., 'commit only these files'
    (('diff',), command_diff, 'Shows the diff'),
    (('goto',), command_goto, 'Goes to the given reference'),
    (('restack',), command_restack, 'Change the parent of the given commit to a different commit')
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

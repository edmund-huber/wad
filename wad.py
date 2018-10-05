#!/usr/bin/env python2
import base64
import glob
import hashlib
import inspect
import os.path
import re
import shutil
import sys

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
    commit = look_up_commit(reference)
    # The repository must be clean.
    if len(diff()) > 0:
        raise UsageException("Can't goto when there are un-committed or stashed changes.")
    # Store the new head.
    head_fn = os.path.join('.wad', 'head')
    try:
        with open(head_fn, 'w') as f:
            f.write(reference)
    except IOError:
        raise UsageException("Broken repository - {} can't be written to!".format(head_fn))


# TODO: multi-tenancy doesn't work. lack of any lockfile means that wad operations can't be interleaved
# TODO: also no way to recover if an operation dies halfway - have a .wad/mirror directory that gets merged in at end?
# or keep multiple directories and point at the current one?

class WadObject(object):

    # use abc.abstract?
    def get_reference(self):
        raise UnreachableException()

    @classmethod
    def _filename(cls, reference):
        return os.path.join('.wad', reference)

    @classmethod
    def load(cls, reference):
        cls._check_reference(reference)
        if not os.path.exists(cls._filename(reference)):
            return None
        with open(cls._filename(reference)) as f:
            _object = cls._load_from_file(reference, f)
        return _object

    def store(self):
        reference = self.get_reference()
        with open(type(self)._filename(reference), 'w') as f:
            self._write_to_file(f)


class Tag(WadObject):

    def __init__(self, reference, head_commit):
        self.reference = reference
        self.head_commit = head_commit

    def get_reference(self):
        return self.reference

    @classmethod
    def _check_reference(cls, reference):
        if re.search(r'^T:[a-z_]+$', reference) is None:
            raise Exception('"{}" is not a valid tag reference.'.format(reference))

    @classmethod
    def _load_from_file(cls, reference, f):
        (head,) = f.readlines()
        head_commit = Commit.load(head)
        if head_commit is None:
            raise Exception("{}, pointed at by {}, doesn't exist!".format(head, reference))
        return Tag(reference, head_commit)

    def _write_to_file(self, f):
        f.write(self.head_commit.get_reference())


def get_head():
    head_fn = os.path.join('.wad', 'head')
    try:
        with open(head_fn) as f:
            (head,) = f.readlines()
            return head
    except IOError:
        raise UsageException('Broken repository - {} does not exist!'.format(head_fn))
    raise UnreachableException()


def new_tag(name, starting_from_commit=None): # TODO: and 'starting from' argument
    if Tag.load(name) is not None:
        raise UsageException('Tag "{}" already exists.'.format(name))
    # TODO name must be a-z and underscores
    if starting_from_commit is None:
        head_commit = look_up_commit(get_head())
        tag = Tag(name, head_commit)
    else:
        tag = Tag(name, starting_from_commit)
    tag.store()
    goto(tag.get_reference())


def command_init():
    """wad init

    Creates a wad in the current directory.
    """
    try:
        os.mkdir('.wad')
    except OSError:
        raise UsageException('Directory {} is already a wad.'.format(os.path.abspath('.')))
    os.mkdir(os.path.join('.wad', 'commits'))
    os.mkdir(os.path.join('.wad', 'topics'))
    init_commit = Commit()
    init_commit.set_description('wad init')
    for dirpath, dirnames, filenames in os.walk('.'):
        if dirpath.startswith(os.path.join('.', '.wad', '')):
            # Don't include files under '.wad/'.
            continue
        for fn in filenames:
            init_commit.add_file(os.path.join(dirpath, fn))
    init_commit.store()
    new_tag('main', starting_from_commit=init_commit)  # TODO tag -> topic


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


def command_tag():
    check_is_wad_repository()
    head_ref = get_head()
    for tag_fn in glob.glob(os.path.join('.wad', 'T:*')):
        tag_ref = os.path.basename(tag_fn)
        print '{} {}'.format(
            '*' if tag_ref == head_ref else ' ',
            tag_ref
        )


def command_new_tag(reference): # TODO optional: starting_from
    if reference is None:
        raise UsageException('"new tag" needs a reference') # TODO: UsageException
    new_tag(reference)


def command_new_commit(description):
    if description is None:
        raise Exception('"new commit" needs a description') # TODO: UsageException
    head = get_head()
    # pack up all the changes in the commit
    commit = Commit(look_up_commit(head).get_reference(), description)
    # TODO for filename in diff(): commit.add_files(..)
    # do not proceed if nothing to commit
    commit.store()
    if head.startswith('T:'):
        tag = Tag.load(head)
        tag.head_commit = commit
        tag.store()
        goto(head)
    elif head.startswith('C:'):
        goto(commit.get_reference())
    else:
        raise UnreachableException()


# TODO: author
# for author, need to read a ~/.wadconfig
class Commit(object):

    def __init__(self, reference=None):
        self.reference = reference

    def _commit_dir(self):
        if self.reference == 'stage':
            return os.path.join('.wad', 'stage')
        else:
            return os.path.join('.wad', 'commits', self.reference)

    def _set_up_stage(self):
        stage_dirname = os.path.join('.wad', 'stage')
        if self.reference != 'stage':
            if os.path.isdir(stage_dirname):
                raise UnreachableException('The stage was not cleaned up.')
            os.mkdir(stage_dirname)
            self.reference = 'stage'

    def get_parent(self):
        # TODO read from reference/parent
        pass

    def set_parent(self, commit):
        self._set_up_stage()
        # TODO write parent into stage
        pass

    def get_description(self):
        # TODO see above
        pass

    def set_description(self, description):
        self._set_up_stage()
        with open(os.path.join(self._commit_dir(), 'description'), 'w') as f:
            f.write(description)

    # After 'add_file' is called once, a Commit is 'staged', meaning that it
    # needs to be store()d.
    # TODO: force use with contexts
    def add_file(self, filename):
        print 'add_file: {}'.format(filename)
        self._set_up_stage()
        path_in_tree = os.path.join(self._commit_dir(), 'tree', filename)
        try:
            os.makedirs(os.path.dirname(path_in_tree))
        except OSError:
            pass
        shutil.copyfile(filename, path_in_tree)

    def store(self):
        if self.reference != 'stage':
            raise InternalException() # TODO
        # Find all files in the commit, sort them, then do a sha1 hash of all
        # filenames and their contents.
        paths = []
        for dirpath, dirnames, filenames in os.walk(self._commit_dir()):
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
        # Move the commit contents to the proper commit directory, and delete
        # stage.
        stage_dirname = os.path.join('.wad', 'stage')
        self.reference = _hash.hexdigest()
        if os.path.isdir(self._commit_dir()):
            raise InternalException()
        shutil.move(stage_dirname, self._commit_dir())
        # TODO: might need to think about copying metadata too -- so that mtime is preserved?

    # TODO: uses of os.walk safe against circular links? does it follow them?

    @classmethod
    def _check_reference(cls, reference):
        if re.search(r'^C:[0-9a-f]+$', reference) is None:
            raise Exception('"{}" is not a valid commit reference.'.format(reference))

    # TODO somehow would like to know/check that a Commit (with the same logical data) has not changed its reference

    @classmethod
    def _load_from_file(cls, reference, f):
        parent_reference = f.readline().strip() or None
        description_len = int(f.readline().strip())
        description = f.read(description_len)
        f.read(1)
        # TODO: be lazy - don't read the entire commit contents, yet
        return Commit(parent_reference, description)

    def _write_to_file(self, f):
        if self.parent_reference is None:
            f.write('\n')
        else:
            f.write(self.parent_reference + '\n')
        f.write(str(len(self.description)) + '\n')
        f.write(self.description + '\n')


def look_up_commit(reference):
    if reference.startswith('C:'):
        return Commit.load(reference)
    elif reference.startswith('T:'):
        tag = Tag.load(reference)
        assert tag is not None
        return Commit.load(tag.head_commit.get_reference())
    else:
        raise UnreachableException() # TODO


def command_goto(reference):
    if reference is None:
        raise Exception('"goto" needs a reference') # TODO: UsageException
    goto(reference)


def command_restack():
    pass # TODO


command_fns = (
    (('help',), command_help, None),
    (('init',), command_init, 'Creates a wad in the current directory'),
    (('status',), command_status, 'Shows the current tag, changes, etc'),
    (('log',), command_log, 'Lists commits from the head backwards'),
    (('tag',), command_tag, 'Lists all tags'),
    (('new', 'tag'), command_new_tag, 'Creates a new tag and goes to it'),
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

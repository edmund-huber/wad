#!/usr/bin/env python2
import inspect
import os.path
import re
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
    commit = look_up_reference(reference)
    # TODO: the actual reading of commits and changing files
    set_head(reference)


class WadObject(object):

    def __init__(self, reference):
        type(self)._check_reference(reference)
        self.reference = reference

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
        with open(type(self)._filename(self.reference), 'w') as f:
            self._write_to_file(f)


class Tag(WadObject):

    def __init__(self, reference, head_commit):
        super(Tag, self).__init__(reference)
        self.head_commit = head_commit

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
        f.write(self.head_commit.reference)


def get_head():
    head_fn = os.path.join('.wad', 'head')
    try:
        with open(head_fn) as f:
            (head,) = f.readlines()
            return head
    except IOError:
        raise UsageException('Broken repository - {} does not exist!'.format(head_fn))
    raise UnreachableException()


def set_head(reference):
    head_fn = os.path.join('.wad', 'head')
    try:
        with open(head_fn, 'w') as f:
            f.write(reference)
    except IOError:
        raise UsageException("Broken repository - {} can't be written to!".format(head_fn))


def new_tag(name, starting_from_commit=None): # TODO: and 'starting from' argument
    if Tag.load(name) is not None:
        raise UsageException('Tag "{}" already exists.'.format(name))
    # TODO name must be a-z and underscores
    if starting_from_commit is None:
        head_commit = get_head()
        tag = Tag(name, head_commit)
    else:
        tag = Tag(name, starting_from_commit)
    tag.store()
    goto(tag.reference)


def command_init():
    """wad init

    Creates a wad in the current directory.
    """
    try:
        os.mkdir('.wad')
    except OSError:
        raise UsageException('Directory {} is already a wad.'.format(os.path.abspath('.')))
    genesis_commit = Commit('C:0', None) # TODO add a 'next_commit_id' -- but not that, because that won't work distributed
    genesis_commit.store()
    new_tag('T:main', starting_from_commit=genesis_commit)


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
    print 'log'


def command_diff():
    print 'log'


def command_tag():
    check_is_wad_repository()
    print 'tag'


def command_new_tag(description):
    if description is None:
        raise UsageException('"new tag" needs a description') # TODO: UsageException
    print 'new tag: "{}"'.format(description)


def command_new_commit(description):
    if description is None:
        raise Exception('"new commit" needs a description') # TODO: UsageException
    head = get_head()
    commit = Commit('C:1', look_up_reference(head).reference)
    commit.store()
    if head.startswith('T:'):
        tag = Tag.load(head)
        tag.head_commit = commit
        tag.store()
        goto(head)
    elif head.startswith('C:'):
        goto(commit.reference)
    else:
        raise UnreachableException()


class Commit(WadObject):

    def __init__(self, reference, parent_reference):
        super(Commit, self).__init__(reference)
        self.parent_reference = parent_reference

    @classmethod
    def _check_reference(cls, reference):
        if re.search(r'^C:[0-9]+$', reference) is None:
            raise Exception('"{}" is not a valid commit reference.'.format(reference))

    @classmethod
    def _load_from_file(cls, reference, f):
        (parent_reference,) = f.readlines()
        # TODO: be lazy - don't read the entire commit contents, yet
        return Commit(reference, parent_reference)

    def _write_to_file(self, f):
        if self.parent_reference is None:
            f.write('\n')
        else:
            f.write(self.parent_reference + '\n')

    # TODO: .parent()


def look_up_reference(reference):
    if reference.startswith('C:'):
        return Commit.load(reference)
    elif reference.startswith('T:'):
        tag = Tag.load(reference)
        return Commit.load(tag.head_commit.reference)
    else:
        raise UnreachableException() # TODO


def command_goto(reference):
    if reference is None:
        raise Exception('"goto" needs a reference') # TODO: UsageException
    goto(reference)


def command_restack():
    pass


command_fns = (
    (('help',), command_help, None),
    (('init',), command_init, 'Creates a wad in the current directory'),
    (('status',), command_status, 'Shows the current tag, changes, etc'),
    (('log',), command_log, 'Lists commits from the head backwards'),
    (('new', 'tag'), command_new_tag, 'Creates a new tag and goes to it'),
    (('new', 'commit'), command_new_commit, 'Creates a new commit on top of head using the diff'),
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

#!/usr/bin/env python2
import inspect
import os.path
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


def command_init():
    """wad init

    Creates a wad in the current directory.
    """
    try:
        os.mkdir('.wad')
    except OSError:
        raise UsageException('Directory {} is already a wad.'.format(os.path.abspath('.')))


def check_is_wad_repository():
    if not os.path.exists('.wad'):
        raise UsageException('Directory {} is not a wad. Try `wad init`.'.format(os.path.abspath('.')))


def command_list():
    check_is_wad_repository()
    print 'list'


def command_new_list(description):
    if description is None:
        raise UsageException('"new list" needs a description') # TODO: UsageException
    print 'new list: "{}"'.format(description)


def command_new_commit(description):
    if description is None:
        raise Exception('"new commit" needs a description') # TODO: UsageException
    print 'new commit: "{}"'.format(description)


def look_up_reference(reference):
    if reference.startswith('C:'):
        return reference, None
    elif reference.startswith('L:'):
        return None, reference
    else:
        raise Exception('"{}" is not a reference'.format(reference))


def command_goto(reference):
    if reference is None:
        raise Exception('"goto" needs a reference') # TODO: UsageException
    commit, _list = look_up_reference(reference)
    print 'goto: "{}" {} {}'.format(reference, commit, _list)


def command_restack():
    pass


command_fns = (
    (('help',), command_help, None),
    (('init',), command_init, 'Creates a wad in the current directory'),
    (('list',), command_list, None),
    (('new', 'list'), command_new_list, None),
    (('new', 'commit'), command_new_commit, None),
    (('goto',), command_goto, None),
    (('restack',), command_restack, None)
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

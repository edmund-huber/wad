#!/usr/bin/env python2
import inspect
import sys


def command_help(*args):
    print 'halp'


def command_list():
    print 'list'


def command_new_list():
    print 'new list'


def command_new_commit():
    pass


def command_goto():
    pass


def command_restack():
    pass


command_fns = (
    (('help',), command_help),
    (('list',), command_list),
    (('new', 'list'), command_new_list),
    (('new', 'commit'), command_new_commit),
    (('goto',), command_goto),
    (('restack',), command_restack)
)
command_prefix = None
command_fn = None
for prefix, fn in command_fns:
    if tuple(sys.argv[1:][:len(prefix)]) == prefix:
        # TODO: catch all exceptions/asserts and print 'an internal error has occured..'
        assert command_prefix is None
        command_prefix = prefix
        command_fn = fn
if command_prefix is None:
    command_prefix = ()
    command_fn = command_help
command_args = sys.argv[1 + len(command_prefix):]
inspect_command_fn = inspect.getargspec(command_fn)
if inspect_command_fn.varargs is not None:
    pass
elif len(command_args) < len(inspect_command_fn.args):
    missing_args = len(inspect_command_fn.args) - len(command_args)
    command_args.extend([None] * missing_args)
elif len(command_args) > len(inspect_command_fn.args):
    raise Exception('blah blah')
command_fn(*command_args)

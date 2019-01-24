from cStringIO import StringIO
import os
import shutil
import sys
import tempfile
import traceback
from unittest import main, TestCase

from wad import command_fns, wad_main, UsageException


class CaptureOutput(object):

    def __init__(self):
        self._stringout = StringIO()
        self._stringerr = StringIO()

    def get_stdout(self):
        return self._stringout.getvalue()

    def get_stderr(self):
        return self._stringerr.getvalue()

    def __enter__(self):
        self._stdout = sys.stdout
        sys.stdout = self._stringout
        self._stderr = sys.stderr
        sys.stderr = self._stringerr
        return self

    def __exit__(self, *args):
        sys.stdout = self._stdout
        sys.stderr = self._stderr


# Figure out where test tempdirs are made and delete all previous test results.
TMP_PREFIX = 'wad_test_'
probe_dir = tempfile.mkdtemp(prefix=TMP_PREFIX)
tmp_root, _ = os.path.split(probe_dir)
for fn in os.listdir(tmp_root):
    if fn.startswith('wad_test_'):
        to_delete = os.path.join(tmp_root, fn)
        shutil.rmtree(to_delete)


class WadTestCase(TestCase):

    def setUp(self):
        # Change directory to a new tempdir to isolate test results.
        self._test_dir = tempfile.mkdtemp(prefix=TMP_PREFIX)
        os.chdir(self._test_dir)

    def wad(self, *command, **kwargs):
        if kwargs.get('error', False):
            with self.assertRaises(UsageException):
                with CaptureOutput() as output:
                    wad_main(command)
        else:
            with CaptureOutput() as output:
                wad_main(command)
        self.wad_output = output.get_stdout()
        # TODO this is probably not always right:
        self.assertEqual(output.get_stderr(), '')


# If .wad doesn't exist, then we should be able to `wad up`.
class TestWadUp(WadTestCase):
    def test(self):
        self.wad('up')
        # And there should be one commit.
        self.wad('commits')
        self.assertRegexpMatches(self.wad_output, r'wad up$')


# If wad already exists, then `wad up` should fail.
class TestWadUpFailsWhenWadExists(WadTestCase):
    def test(self):
        self.wad('up')
        self.wad('up', error=True)


class TestAllCommandsMeta(type):
    def __new__(cls, name, bases, _dict):
        def gen_test(command):
            def test(self):
                _dict['meta_test_fn'](self, *command)
            return test
        for command_tup, _, _ in command_fns:
            command = ' '.join(command_tup)
            if command not in _dict['meta_exclude_commands']:
                test_name = 'test_%s' % command.replace(' ', '_')
                _dict[test_name] = gen_test(command.split(' '))
        return type.__new__(cls, name, bases, _dict)


# If .wad doesn't exist, then all commands except for `help` and `up` should
# fail.
def check_command_fails_if_no_wad_directory(self, *command):
    self.wad(*command, error=True)
class TestAllCommandsNeedAWad(WadTestCase):
    __metaclass__ = TestAllCommandsMeta
    meta_exclude_commands = ['help', 'up']
    meta_test_fn = check_command_fails_if_no_wad_directory


# All commands must have a 'help' entry.
def check_command_has_help(self, *command):
    help_command = ('help',) + command
    self.wad(*help_command)
class TestAllCommandsMustHaveHelp(WadTestCase):
    __metaclass__ = TestAllCommandsMeta
    meta_exclude_commands = []
    meta_test_fn = check_command_has_help


class TestWadStatus(WadTestCase):
    def test(self):
        # After `wad up`, status doesn't have anything interesting to say.
        self.wad('up')
        self.wad('status')
        self.assertRegexpMatches(self.wad_output, r'No changes.$')
        # But if there are changes, they will show up in status.
        os.mkdir('a')
        os.mkdir('a/b')
        with open('a/b/1', 'w') as f:
            f.write('1')
        with open('a/b/2', 'w') as f:
            f.write('2')
        with open('a/3', 'w') as f:
            f.write('3')
        with open('4', 'w') as f:
            f.write('4')
        os.mkdir('c')
        self.wad('status')
        self.assertEqual(self.wad_output,
            'topic/main "TODO - new topic"\n'
            'latest commit: wad up\n'
            '+ d   ./a\n'
            '+ f       3\n'
            '+ d       b\n'
            '+ f         1\n'
            '+ f         2\n'
            '+ d     c\n'
            '+ f     4\n'
        )
        # When we commit, there are no changes to show anymore.
        self.wad('new', 'commit', 'test')
        self.wad('status')
        self.assertRegexpMatches(self.wad_output, r'o changes.$')
        # Make some more changes show up.
        shutil.rmtree('a/b')
        with open('a/3', 'w') as f:
            f.write('aaa')
        with open('c/5', 'w') as f:
            f.write('5')
        with open('c/6', 'w') as f:
            f.write('6')
        self.wad('status')
        self.assertEqual(self.wad_output,
            'topic/main "TODO - new topic"\n'
            'latest commit: test\n'
            '~ f   ./a/3\n'
            '- d       b\n'
            '- f         1\n'
            '- f         2\n'
            '+ f     c/5\n'
            '+ f       6\n'
        )
        # Once again, they go away after a commit.
        self.wad('new', 'commit', 'changed stuff')
        self.wad('status')
        self.assertRegexpMatches(self.wad_output, r'No changes.$')


class TestWadTopics(WadTestCase):
    def test(self):
        # At first, it should just show the main topic.
        self.wad('up')
        self.wad('topics')
        self.assertEqual(self.wad_output, '*  topic/main  "TODO - new topic"\n')
        # If a new topic is created, that new topic will be active.
        self.wad('new', 'topic', 'test')
        self.wad('topics')
        self.assertEqual(self.wad_output,
            '*  topic/test  "TODO - new topic"\n'
            '   topic/main  "TODO - new topic"\n'
        )


class TestWadCommit(WadTestCase):
    def test(self):
        # After `wad up`, there's only one commit.
        self.wad('up')
        self.wad('commits')
        self.assertRegexpMatches(self.wad_output, r'^commit/\S+    wad up$')
        # After we commit a change, there will be two commits.
        with open('new_file', 'w') as f:
            f.write('a')
        self.wad('new', 'commit', 'new file')
        self.wad('commits')
        self.assertRegexpMatches(self.wad_output,
            r'^commit/\S+    new file\n'
            r'commit/\S+    wad up$'
        )


class TestWadNewTopic(WadTestCase):
    """
    'new topic' should fail if there are any uncommitted changes in the tree.
    """
    def test(self):
        self.wad('up')
        with open('new_file', 'w') as f:
            f.write('a')
        self.wad('new', 'topic', 'test', error=True)


class TestWadGoto(WadTestCase):
    def test(self):
        self.wad('up')
        self.wad('new', 'topic', 'test')
        with open('a', 'w') as f:
            f.write('a')
        self.wad('status')
        self.assertEqual(self.wad_output,
            'topic/test "TODO - new topic"\n'
            'latest commit: wad up\n'
            '+ f   ./a\n'
        )
        self.wad('new', 'commit', 'test')
        self.wad('status')
        self.assertRegexpMatches(self.wad_output, r'No changes.$')
        self.wad('goto', 'main')
        self.assertFalse(os.path.isfile('a'))
        self.wad('goto', 'test')
        self.assertTrue(os.path.isfile('a'))


class TestWadReset(WadTestCase):
    def test(self):
        self.wad('up')
        with open('a', 'w') as f:
            f.write('a')
        self.wad('new', 'commit', 'test')
        self.wad('new', 'topic', 'test')
        with open('b', 'w') as f:
            f.write('b')
        self.wad('new', 'commit', 'test again')
        os.unlink('a')
        with open('b', 'w') as f:
            f.write('b2')
        with open('c', 'w') as f:
            f.write('c')
        self.wad('status')
        self.assertEqual(self.wad_output,
            'topic/test "TODO - new topic"\n'
            'latest commit: test again\n'
            '- f   ./a\n'
            '+ f     c\n'
            '~ f     b\n'
        )
        self.wad('reset')
        self.wad('status')
        self.assertRegexpMatches(self.wad_output, r'No changes.$')


if __name__ == '__main__':
    main()

"""Host tuning tests with fake sysctl/install/sudo; never change the test host."""
import os
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest

SCRIPT = Path(__file__).with_name('host-limits.sh')
# The Linux path reads the real /etc/os-release for the supported-host check.
LINUX_ONLY = unittest.skipUnless(sys.platform == 'linux', 'needs a Linux /etc/os-release')

class LimitsTests(unittest.TestCase):
    def run_case(self, instances='128', watches='8192', os_name='Linux', fail_apply=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for key, value in [('instances', instances), ('watches', watches)]:
                (root/key).write_text(value)
            mocks = {
                'uname': '[ "$1" = -m ] && echo x86_64 || echo "$TEST_OS"',
                'sudo': 'exec "$@"',
                'install': '''if [ "$1" = -d ]; then exit 0; fi
cp "$3" "$TEST_ROOT/config"
''',
                'sysctl': '''case "$*" in
"-n fs.inotify.max_user_instances") cat "$TEST_ROOT/instances";;
"-n fs.inotify.max_user_watches") cat "$TEST_ROOT/watches";;
-p*)
[ "$TEST_FAIL" != 1 ] || exit 1
sed -n 's/fs.inotify.max_user_instances=//p' "$TEST_ROOT/config" > "$TEST_ROOT/instances"
sed -n 's/fs.inotify.max_user_watches=//p' "$TEST_ROOT/config" > "$TEST_ROOT/watches";;
esac''',
            }
            for name, body in mocks.items():
                f=root/name; f.write_text('#!/bin/sh\n'+body); f.chmod(0o755)
            env=dict(os.environ,PATH=tmp+':'+os.environ['PATH'],TEST_ROOT=tmp,TEST_OS=os_name,TEST_FAIL=str(int(fail_apply)))
            result=subprocess.run(['bash',str(SCRIPT)],env=env,text=True,capture_output=True)
            config=(root/'config').read_text() if (root/'config').exists() else None
            return result, config
    @LINUX_ONLY
    def test_raise_low_limits(self):
        r,c=self.run_case(); self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('max_user_instances=1024',c); self.assertIn('max_user_watches=1048576',c)
    @LINUX_ONLY
    def test_preserve_higher_limit(self):
        r,c=self.run_case(instances='4096'); self.assertEqual(r.returncode,0,r.stderr)
        self.assertIn('max_user_instances=4096',c)
    @LINUX_ONLY
    def test_sufficient_no_write(self):
        r,c=self.run_case('4096','2097152'); self.assertEqual(r.returncode,0); self.assertIsNone(c)
    def test_macos_refused_without_write(self):
        r,c=self.run_case(os_name='Darwin'); self.assertNotEqual(r.returncode,0); self.assertIsNone(c)
    def test_invalid_read_stops(self):
        r,c=self.run_case(instances='invalid'); self.assertNotEqual(r.returncode,0); self.assertIsNone(c)
    def test_failed_apply_stops(self):
        r,c=self.run_case(fail_apply=True); self.assertNotEqual(r.returncode,0)

if __name__ == '__main__': unittest.main()

"""Run: python3 tests/test_persistence.py  (DB_PATH inside a not-yet-existing dir, e.g. a mounted disk at /var/data)"""
import os, sys, tempfile, subprocess, unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CODE = r'''
import os, sys
sys.path.insert(0, %r)
import server
server.init_db()
P = dict(product="buy", mode="pickup", name="A", phone="2695550100", email="a@example.com", size="20")
if sys.argv[1] == "write":
    print(server.create_order(P))
else:
    c = server.db(); print(c.execute("select count(*) from orders where id=?", (sys.argv[2],)).fetchone()[0])
''' % ROOT

class Persistence(unittest.TestCase):
    def run_py(self, env, *args):
        e = dict(os.environ, VOICE_SECRET="vs", **env)
        e.pop("SMTP_HOST", None)
        r = subprocess.run([sys.executable, "-c", CODE, *args], env=e, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().splitlines()[-1]

    def test_creates_missing_dir_and_survives_restart(self):
        base = tempfile.mkdtemp()
        db = os.path.join(base, "var", "data", "yards.db")    # directory does not exist yet
        env = {"DB_PATH": db}
        oid = self.run_py(env, "write")
        self.assertTrue(os.path.isfile(db))
        self.assertEqual(self.run_py(env, "read", oid), "1")   # fresh process = simulated restart

    def test_default_path_unchanged(self):
        e = {k: v for k, v in os.environ.items() if k != "DB_PATH"}
        r = subprocess.run([sys.executable, "-c", f"import sys; sys.path.insert(0,{ROOT!r}); import server; print(server.DB)"],
                           env=e, capture_output=True, text=True, cwd=ROOT)
        self.assertEqual(r.stdout.strip(), os.path.join(ROOT, "yards.db"))

    def test_entrypoint_syntax(self):
        self.assertEqual(subprocess.run(["sh", "-n", os.path.join(ROOT, "entrypoint.sh")]).returncode, 0)

if __name__ == "__main__": unittest.main()

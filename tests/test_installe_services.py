"""Installateur launchd (`scripts/installe_services.sh`) — course entre bootout et bootstrap.

Bug du 2026-11-21 : `launchctl bootout` rend la main avant la fin du déchargement ; le
`bootstrap` immédiat échouait (« Bootstrap failed: 5 ») et, avec `set -e`, le script
s'arrêtait en laissant ui, puis prediction, arrêtés.

Ici `launchctl`, `uname`, `plutil` et `sleep` sont simulés (launchd n'existe que sous macOS).
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(RACINE, "scripts", "installe_services.sh")

# Faux launchctl : `print` réussit tant que le service est chargé ou en cours de déchargement
# (TEARDOWN essais), `bootstrap` échoue (code 5) pendant ce déchargement, puis FAIL_BOOTSTRAP
# fois de plus si demandé.
FAUX_LAUNCHCTL = r"""#!/bin/sh
E="$STATE"
case "$1" in
  print)
    if [ -f "$E/teardown" ]; then
      n=$(cat "$E/teardown")
      if [ "$n" -gt 0 ]; then echo $((n - 1)) > "$E/teardown"; exit 0; fi
    fi
    [ -f "$E/loaded" ] && exit 0
    exit 1 ;;
  bootout)
    rm -f "$E/loaded"; echo "$TEARDOWN" > "$E/teardown"; exit 0 ;;
  bootstrap)
    if [ -f "$E/teardown" ] && [ "$(cat "$E/teardown")" -gt 0 ]; then exit 5; fi
    f=$(cat "$E/fail" 2>/dev/null || echo "${FAIL_BOOTSTRAP:-0}")
    if [ "$f" -gt 0 ]; then echo $((f - 1)) > "$E/fail"; exit 5; fi
    touch "$E/loaded"; exit 0 ;;
esac
exit 0
"""


class TestInstalleurLaunchd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.bin = os.path.join(self.tmp, "bin")
        self.etat = os.path.join(self.tmp, "etat")
        os.makedirs(self.bin)
        os.makedirs(self.etat)
        for nom, contenu in (
            ("launchctl", FAUX_LAUNCHCTL),
            ("uname", "#!/bin/sh\necho Darwin\n"),
            ("plutil", "#!/bin/sh\nexit 0\n"),
            ("sleep", "#!/bin/sh\nexit 0\n"),
        ):
            chemin = os.path.join(self.bin, nom)
            with open(chemin, "w", encoding="utf-8") as fh:
                fh.write(contenu)
            os.chmod(chemin, 0o755)
        # Le service existe déjà (cas d'une réinstallation).
        open(os.path.join(self.etat, "loaded"), "w").close()

    def lance(self, *services, teardown=2, fail_bootstrap=0):
        env = dict(os.environ)
        env.update(
            HOME=self.tmp,
            STATE=self.etat,
            TEARDOWN=str(teardown),
            FAIL_BOOTSTRAP=str(fail_bootstrap),
            PATH=self.bin + os.pathsep + env["PATH"],
        )
        return subprocess.run(
            ["sh", SCRIPT, *services], capture_output=True, text=True, env=env, timeout=60
        )

    def test_syntaxe_du_script(self):
        resultat = subprocess.run(["sh", "-n", SCRIPT], capture_output=True, text=True)
        self.assertEqual(resultat.returncode, 0, resultat.stderr)

    def test_bootstrap_attend_la_fin_du_bootout(self):
        resultat = self.lance("prediction", teardown=2)
        self.assertEqual(resultat.returncode, 0, resultat.stdout + resultat.stderr)
        self.assertIn("démarré : prediction", resultat.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.etat, "loaded")))

    def test_bootstrap_rejoue_apres_un_echec(self):
        resultat = self.lance("ui", teardown=0, fail_bootstrap=2)
        self.assertEqual(resultat.returncode, 0, resultat.stdout + resultat.stderr)
        self.assertIn("démarré : ui", resultat.stdout)

    def test_un_echec_definitif_est_signale_et_n_arrete_pas_les_autres(self):
        # ui échoue 4 fois de suite ; prediction, derrière, doit quand même être tenté.
        resultat = self.lance("ui", "prediction", teardown=0, fail_bootstrap=4)
        self.assertNotEqual(resultat.returncode, 0)
        self.assertIn("ui n'a pas démarré", resultat.stderr)
        self.assertIn("démarré : prediction", resultat.stdout)


if __name__ == "__main__":
    unittest.main()

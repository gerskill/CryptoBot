"""Bornage du journal d'entonnoir — lecture par la fin et rotation.

LE BUG VERROUILLÉ ICI. `recent_flow()` parsait le fichier ENTIER pour n'en
garder que les 20 dernières lignes utiles. Mesuré au 2026-08-02 :
`data/funnel_log.jsonl` faisait 11 Mo pour 61 922 lignes, et le fichier
n'avait aucune rotation — il ne pouvait que grandir. Or `recent_flow` est
appelée depuis `LearningEngine._starving`, donc à CHAQUE `_bounded_set`, et
pour chacun des 7 bras.

Les deux propriétés à ne pas casser :

  1. `tail` ne change PAS la valeur retournée. Une optimisation de lecture qui
     modifie la mesure serait pire que le coût qu'elle évite.
  2. Après une rotation, `recent_flow` voit toujours quelque chose. Un flux
     inconnu (`None`) autorise à resserrer un filtre : devenir aveugle à
     chaque bascule rouvrirait exactement la boucle que `_starving` ferme.
"""

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.funnel import (  # noqa: E402
    FunnelRecorder,
    read_funnel,
    recent_flow,
    rotate,
    rotated_path,
)

ARM = "sniper"
SEPT_BRAS = ("baseline", ARM, "scalp", "runner", "quality", "narrative", "consensus")


def _ligne(arm: str, kept: int, ts: float) -> str:
    return json.dumps(
        {"ts": ts, "arm": arm, "token": "", "symbol": "", "gate": "filtres_total",
         "passed": kept > 0, "reason": "", "kept": kept, "rejected": 50 - kept},
        ensure_ascii=False,
    )


def _bruit(arm: str, ts: float, index: int) -> str:
    """Une ligne d'une AUTRE porte : c'est elle qui dilue les `filtres_total`.

    Mesuré : 68 lignes par cycle et par bras, dont une seule `filtres_total`.
    Sans ce bruit le test lirait un fichier irréaliste où `tail` serait
    toujours largement suffisant.
    """
    return json.dumps(
        {"ts": ts, "arm": arm, "token": f"T{index}", "symbol": f"S{index}",
         "gate": "seuil_alpha", "passed": False, "reason": "alpha 40 < 65"},
        ensure_ascii=False,
    )


def _ecrire_journal(path: str, cycles: int, arms: tuple[str, ...], bruit: int) -> None:
    """Journal synthétique au même profil que le vrai : 1 `filtres_total` par
    bras et par cycle, noyée dans `bruit` lignes d'autres portes."""
    with open(path, "w", encoding="utf-8") as fh:
        for cycle in range(cycles):
            ts = 1_000_000.0 + cycle
            for arm in arms:
                fh.write(_ligne(arm, kept=cycle % 7, ts=ts) + "\n")
                for i in range(bruit):
                    fh.write(_bruit(arm, ts, i) + "\n")


class TailNeChangePasLaMesure(unittest.TestCase):
    """`tail` est une optimisation de lecture, pas un changement de résultat."""

    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "funnel_log.jsonl")
        self.addCleanup(self.dir.cleanup)

    def test_meme_mediane_avec_et_sans_tail(self):
        # 9 800 lignes : 140 cycles x 7 bras x (1 + 9) lignes — au-dessus du
        # tail de 20 x 250 = 5 000, donc la lecture est VRAIMENT tronquée.
        _ecrire_journal(self.path, cycles=140, arms=SEPT_BRAS, bruit=9)
        self.assertEqual(len(read_funnel(self.path)), 9800)
        self.assertLess(len(read_funnel(self.path, tail=5000)), 9800)

        complet = [
            r for r in read_funnel(self.path)
            if r.get("gate") == "filtres_total" and r.get("arm") == ARM
        ]
        attendu = sorted(int(r["kept"]) for r in complet[-20:])[10]

        self.assertEqual(recent_flow(self.path, ARM), float(attendu))

    def test_tail_borne_vraiment_la_lecture(self):
        _ecrire_journal(self.path, cycles=140, arms=SEPT_BRAS, bruit=9)

        self.assertEqual(len(read_funnel(self.path)), 9800)
        self.assertEqual(len(read_funnel(self.path, tail=100)), 100)

    def test_fichier_absent_ne_leve_pas(self):
        absent = os.path.join(self.dir.name, "rien.jsonl")
        self.assertEqual(read_funnel(absent, tail=50), [])
        self.assertIsNone(recent_flow(absent, ARM))

    def test_ligne_corrompue_ignoree_meme_avec_tail(self):
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(_ligne(ARM, 3, 1.0) + "\n")
            fh.write("{ceci n'est pas du json\n")
            fh.write("\n")
            fh.write(_ligne(ARM, 5, 2.0) + "\n")

        self.assertEqual(len(read_funnel(self.path, tail=10)), 2)


class Rotation(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "funnel_log.jsonl")
        self.addCleanup(self.dir.cleanup)

    def test_sous_le_seuil_rien_ne_bouge(self):
        _ecrire_journal(self.path, cycles=5, arms=(ARM,), bruit=2)
        self.assertFalse(rotate(self.path, max_bytes=1_000_000))
        self.assertFalse(os.path.exists(rotated_path(self.path)))

    def test_au_dessus_du_seuil_bascule(self):
        _ecrire_journal(self.path, cycles=50, arms=(ARM,), bruit=2)
        self.assertTrue(rotate(self.path, max_bytes=100))
        self.assertTrue(os.path.exists(rotated_path(self.path)))
        self.assertFalse(os.path.exists(self.path))

    def test_fichier_absent_ne_leve_pas(self):
        self.assertFalse(rotate(os.path.join(self.dir.name, "rien.jsonl")))

    def test_lecture_enchaine_generation_precedente_puis_courante(self):
        with open(rotated_path(self.path), "w", encoding="utf-8") as fh:
            fh.write(_ligne(ARM, 1, 1.0) + "\n")
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(_ligne(ARM, 2, 2.0) + "\n")

        rows = read_funnel(self.path)
        self.assertEqual([r["kept"] for r in rows], [1, 2])

    def test_recent_flow_voit_encore_apres_une_rotation(self):
        """Le cas qui compte : juste après la bascule, le fichier courant est
        quasi vide. Sans la génération précédente, `recent_flow` retournerait
        `None` — donc « flux inconnu », donc resserrage autorisé."""
        _ecrire_journal(self.path, cycles=40, arms=(ARM,), bruit=2)
        avant = recent_flow(self.path, ARM)
        self.assertIsNotNone(avant)

        self.assertTrue(rotate(self.path, max_bytes=100))
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(_ligne(ARM, 3, 9_000.0) + "\n")

        self.assertIsNotNone(recent_flow(self.path, ARM))

    def test_flush_declenche_la_rotation(self):
        recorder = FunnelRecorder(path=self.path)
        for i in range(200):
            recorder.record(ARM, f"T{i}", f"S{i}", "filtres", False, "liquidité")
        recorder.flush()

        import src.core.funnel as funnel

        original = funnel.FUNNEL_MAX_BYTES
        funnel.FUNNEL_MAX_BYTES = 100
        self.addCleanup(setattr, funnel, "FUNNEL_MAX_BYTES", original)

        recorder.record(ARM, "T", "S", "filtres", False, "liquidité")
        recorder.flush()

        self.assertTrue(os.path.exists(rotated_path(self.path)))


if __name__ == "__main__":
    unittest.main()

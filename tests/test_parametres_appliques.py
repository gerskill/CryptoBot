"""Chaque paramètre déclaré dans la config doit être lu par le code, ou
déclaré explicitement « non appliqué » avec sa raison.

Bug verrouillé : `entry_rules.min_liquidity_lock_days` figurait dans
`config/params.json` alors qu'aucun code ne le lit (RugCheck n'expose aucune
durée de verrou). Un lecteur de la config croyait à une protection absente.
Le test lit les fichiers de `config/` sans jamais les écrire : ils sont
réécrits à l'exécution par l'apprentissage.
"""

import ast
import glob
import json
import os
import sys
import unittest

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, RACINE)

from src.core.params import PARAMETRES_LUS_DYNAMIQUEMENT, PARAMETRES_NON_APPLIQUES  # noqa: E402

SECTIONS = ("filters", "entry_rules", "exit_rules", "risk_rules")
# Les deux dictionnaires vivent dans src/ : leurs propres littéraux ne
# doivent pas compter comme « lecture », sinon le test 2 échouerait par
# construction.
NOMS_EXCLUS = ("PARAMETRES_NON_APPLIQUES", "PARAMETRES_LUS_DYNAMIQUEMENT")


def _cibles(noeud) -> list[str]:
    if isinstance(noeud, ast.Assign):
        cibles = noeud.targets
    elif isinstance(noeud, ast.AnnAssign):
        cibles = [noeud.target]
    else:
        return []
    return [c.id for c in cibles if isinstance(c, ast.Name)]


def _docstrings(arbre) -> set[int]:
    """Ids des noeuds Constant qui sont des docstrings (module, classe, fonction)."""
    ids = set()
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            corps = noeud.body
            if corps and isinstance(corps[0], ast.Expr) and isinstance(corps[0].value, ast.Constant) \
                    and isinstance(corps[0].value.value, str):
                ids.add(id(corps[0].value))
    return ids


def chaines_du_code() -> set[str]:
    """Littéraux chaînes de src/**/*.py, hors docstrings et hors les deux
    dictionnaires de déclaration. Une chaîne pointée compte aussi pour son
    dernier segment."""
    trouvees: set[str] = set()
    for chemin in glob.glob(os.path.join(RACINE, "src", "**", "*.py"), recursive=True):
        with open(chemin, encoding="utf-8") as fh:
            arbre = ast.parse(fh.read(), filename=chemin)
        exclus = _docstrings(arbre)
        for noeud in ast.walk(arbre):
            if any(n in NOMS_EXCLUS for n in _cibles(noeud)):
                exclus.update(id(x) for x in ast.walk(noeud))
        for noeud in ast.walk(arbre):
            if isinstance(noeud, ast.Constant) and isinstance(noeud.value, str) and id(noeud) not in exclus:
                trouvees.add(noeud.value)
                if "." in noeud.value:
                    trouvees.add(noeud.value.rsplit(".", 1)[1])
    return trouvees


def cles_declarees() -> dict[str, list[str]]:
    """chemin pointé -> fichiers de config qui le déclarent."""
    fichiers = [os.path.join(RACINE, "config", "params.json")]
    fichiers += sorted(glob.glob(os.path.join(RACINE, "config", "arms", "*.json")))
    declarees: dict[str, list[str]] = {}
    for fichier in fichiers:
        with open(fichier, encoding="utf-8") as fh:
            document = json.load(fh)
        for section in SECTIONS:
            bloc = document.get(section)
            if not isinstance(bloc, dict):
                continue
            for cle in bloc:
                if cle.startswith("_"):
                    continue
                declarees.setdefault(f"{section}.{cle}", []).append(os.path.relpath(fichier, RACINE))
    return declarees


class TestChaqueParametreEstLuOuDeclare(unittest.TestCase):
    def test_aucun_parametre_declare_nest_orphelin(self):
        chaines = chaines_du_code()
        orphelines = {
            chemin: fichiers
            for chemin, fichiers in cles_declarees().items()
            if chemin.split(".", 1)[1] not in chaines
            and chemin not in PARAMETRES_NON_APPLIQUES
            and chemin not in PARAMETRES_LUS_DYNAMIQUEMENT
        }
        self.assertFalse(
            orphelines,
            "Paramètres déclarés mais lus nulle part : "
            + ", ".join(f"{c} ({fs[0]})" for c, fs in sorted(orphelines.items()))
            + " — la lire dans le code ou l'ajouter à PARAMETRES_NON_APPLIQUES avec sa raison.",
        )

    def test_une_cle_declaree_non_appliquee_est_vraiment_absente_du_code(self):
        chaines = chaines_du_code()
        menteuses = [c for c in PARAMETRES_NON_APPLIQUES if c.split(".", 1)[1] in chaines]
        self.assertFalse(menteuses, f"Déclarées non appliquées mais lues par le code : {menteuses}")

    def test_la_cle_du_verrou_de_liquidite_est_declaree_non_appliquee(self):
        self.assertIn("entry_rules.min_liquidity_lock_days", PARAMETRES_NON_APPLIQUES)

    def test_chaque_raison_est_renseignee(self):
        for dico in (PARAMETRES_NON_APPLIQUES, PARAMETRES_LUS_DYNAMIQUEMENT):
            for cle, raison in dico.items():
                self.assertTrue(raison.strip(), f"{cle} sans raison")


if __name__ == "__main__":
    unittest.main()

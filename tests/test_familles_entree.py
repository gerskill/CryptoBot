"""Familles d'entrée E0-E7 et mise en pause des doublons (2026-11-23).

Verrouille :
  - chaque porte de famille (cas qui passe, cas qui refuse, donnée absente) ;
  - le consensus des familles (le hasard ne vote pas, un membre doit passer
    SES filtres, SON seuil et SA porte) ;
  - le manifeste : 8 bras `entree_*` à sortie de référence IDENTIQUE, mise
    fixe 1 %, apprentissage coupé, aucune porte Helius, aucun réglage mort ;
    7 doublons en pause (pas supprimés) ; idempotence ; somme des parts ;
  - le bras en pause : construit (ses positions sont suivies), mais n'apprend
    plus, ne vote plus, n'entre plus ;
  - `_try_entries` : porte de famille, pas d'analyse technique quand la
    famille le demande, mise fixe ;
  - le suivi du prix 6 h après la sortie (journal de recherche).
"""

import copy
import json
import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src import main as main_module  # noqa: E402
from src.core import familles  # noqa: E402
from src.core.arm import bootstrap_arms  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.params import PARAMETRES_NON_APPLIQUES  # noqa: E402
from src.core.research_log import DEFAULTS as RESEARCH_DEFAULTS, ResearchJournal  # noqa: E402
from tests.test_arms import ArmsTestCase  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import familles_entree_manifeste as manifeste_script  # noqa: E402

PAUSES = ("sniper_ar", "sniper_young", "tp1_20", "tp1_20_vl2", "score_v2",
          "anti_rug", "consensus_af")
NOUVEAUX = ("entree_hasard", "entree_score", "entree_securite", "entree_wallets",
            "entree_acheteurs", "entree_repli", "entree_mature", "entree_consensus")


def cand(addr="T", alpha=60.0, **kw):
    return Candidate(token_address=addr, symbol=addr, name=addr, chain="solana", **kw).with_fields(
        alpha_score_absolute=alpha, alpha_score=alpha)


SUR = dict(mint_authority_revoked=True, freeze_authority_revoked=True, lp_locked_pct=100.0,
           rugcheck_score=95.0, top10_holder_pct=20.0, dev_wallet_pct=0.0,
           bundler_rate=0.1, insider_rate=0.05, is_wash_trading=False)


class TestPortes(unittest.TestCase):
    def test_hasard_suit_le_tirage(self):
        self.assertTrue(familles.porte("hasard", cand(), {}, rng=lambda: 0.1)[0])
        ok, why = familles.porte("hasard", cand(), {}, rng=lambda: 0.5)
        self.assertFalse(ok)
        self.assertIn("tirage", why)
        regles = {"famille_reglages": {"probabilite": 0.6}}
        self.assertTrue(familles.porte("hasard", cand(), regles, rng=lambda: 0.5)[0])

    def test_score_et_mature_laissent_passer(self):
        self.assertTrue(familles.porte("score", cand())[0])
        self.assertTrue(familles.porte("mature", cand())[0])

    def test_securite(self):
        self.assertTrue(familles.porte("securite", cand(**SUR))[0])
        cas = {
            "mint": dict(mint_authority_revoked=None),
            "freeze": dict(freeze_authority_revoked=False),
            "LP": dict(lp_locked_pct=None),
            "rugcheck": dict(rugcheck_score=80.0),
            "top10": dict(top10_holder_pct=35.0),
            "dev": dict(dev_wallet_pct=6.0),
            "bundlers": dict(bundler_rate=0.4),
            "insiders": dict(insider_rate=0.2),
            "wash": dict(is_wash_trading=True),
        }
        for mot, over in cas.items():
            ok, why = familles.porte("securite", cand(**{**SUR, **over}))
            self.assertFalse(ok, mot)
            self.assertIn(mot, why)
        # Donnée secondaire absente : ne refuse pas.
        self.assertTrue(familles.porte("securite", cand(**{**SUR, "top10_holder_pct": None}))[0])

    def test_wallets(self):
        self.assertTrue(familles.porte("wallets", cand(smart_money_wallets_30m=2,
                                                      smart_money_buys_30m=3))[0])
        self.assertFalse(familles.porte("wallets", cand(smart_money_wallets_30m=1))[0])
        self.assertFalse(familles.porte("wallets", cand())[0])  # inconnu = refus
        ok, why = familles.porte("wallets", cand(smart_money_wallets_30m=3, smart_money_buys_30m=3,
                                                 smart_money_sells_30m=4))
        self.assertFalse(ok)
        self.assertIn("vendent", why)

    def test_acheteurs(self):
        bon = dict(buys_5m=80, sells_5m=30, price_change_5m=4.0, volume_5m=6000.0,
                   volume_1h=24000.0)
        self.assertTrue(familles.porte("acheteurs", cand(**bon))[0])
        for over in (dict(buys_5m=10), dict(sells_5m=80), dict(price_change_5m=15.0),
                     dict(price_change_5m=0.0), dict(volume_5m=2000.0), dict(volume_1h=0.0)):
            self.assertFalse(familles.porte("acheteurs", cand(**{**bon, **over}))[0], over)

    def test_repli(self):
        bon = dict(price_change_1h=40.0, price_change_5m=-6.0, market_cap=80_000.0,
                   history_highest_market_cap=100_000.0)
        self.assertTrue(familles.porte("repli", cand(**bon))[0])
        for over in (dict(price_change_1h=10.0), dict(price_change_5m=2.0),
                     dict(price_change_5m=-20.0), dict(market_cap=99_000.0),
                     dict(market_cap=40_000.0)):
            self.assertFalse(familles.porte("repli", cand(**{**bon, **over}))[0], over)
        # Sommet inconnu : il faut un vrai recul sur 5 min.
        sans = dict(price_change_1h=40.0, price_change_5m=-1.0)
        self.assertFalse(familles.porte("repli", cand(**sans))[0])
        self.assertTrue(familles.porte("repli", cand(**{**sans, "price_change_5m": -5.0}))[0])

    def test_famille_inconnue_refuse(self):
        self.assertFalse(familles.porte("astrologie", cand())[0])


def _evaluation(*candidats):
    return SimpleNamespace(result=SimpleNamespace(candidates=tuple(candidats)))


class TestConsensus(unittest.TestCase):
    def test_compte_les_familles_d_accord(self):
        c = cand(alpha=60, **SUR, smart_money_wallets_30m=3, smart_money_buys_30m=3)
        membres = [
            ("entree_score", _evaluation(c), 55, {"famille": "score"}),
            ("entree_securite", _evaluation(c), 0, {"famille": "securite"}),
            ("entree_wallets", _evaluation(c), 0, {"famille": "wallets"}),
            ("entree_repli", _evaluation(c), 0, {"famille": "repli"}),  # refuse
            ("entree_hasard", _evaluation(c), 0, {"famille": "hasard"}),  # ne vote pas
            ("absent", None, 0, {"famille": "score"}),
        ]
        accord, juges = familles.accords_consensus(c, membres)
        self.assertEqual(accord, ["entree_score", "entree_securite", "entree_wallets"])
        self.assertEqual(len(juges), 4)

    def test_seuil_et_filtres_du_membre(self):
        c = cand(alpha=50)
        autre = cand("U", alpha=90)
        membres = [
            ("entree_score", _evaluation(c), 55, {"famille": "score"}),   # sous son seuil
            ("entree_mature", _evaluation(autre), 0, {"famille": "mature"}),  # pas dans son lot
        ]
        self.assertEqual(familles.accords_consensus(c, membres)[0], [])


def _manifeste_production():
    with open(os.path.join(ROOT, "config", "strategies.json"), encoding="utf-8") as fh:
        return json.load(fh)


def _mature_doc():
    return {"filters": {"min_liquidity_usd": 250000, "min_age_hours": 24.0, "max_age_hours": 720.0,
                        "min_volume_1h": 20000, "max_tax_buy": 5, "_note": "x"},
            "scan": {"mature_source": {"refresh_cycles": 10}}}


class TestManifeste(unittest.TestCase):
    def test_script_idempotent_et_complet(self):
        avant = _manifeste_production()
        # Repartir d'un manifeste SANS les ajouts pour tester l'application.
        brut = copy.deepcopy(avant)
        brut["arms"] = [a for a in brut["arms"] if not a["name"].startswith("entree_")]
        for a in brut["arms"]:
            a.pop("paused", None)
            a.pop("role_label", None)
        doc, notes = manifeste_script.appliquer(brut, _mature_doc())
        self.assertEqual(len([n for n in notes if n.startswith("ajout")]), 8)
        self.assertEqual(len([n for n in notes if n.startswith("pause")]), 7)
        doc2, notes2 = manifeste_script.appliquer(doc, _mature_doc())
        self.assertEqual(notes2, [])
        self.assertEqual(doc2, doc)
        noms_avant = {a["name"] for a in brut["arms"]}
        self.assertTrue(noms_avant <= {a["name"] for a in doc["arms"]}, "rien n'est supprimé")

    def test_production(self):
        doc = _manifeste_production()
        bras = {a["name"]: a for a in doc["arms"]}
        for nom in PAUSES:
            self.assertIs(bras[nom].get("paused"), True, nom)
            self.assertTrue(bras[nom].get("enabled", True), nom)
            self.assertTrue(bras[nom]["role_label"].startswith("En pause"), nom)
        actifs = [a for a in doc["arms"] if a.get("enabled", True) and not a.get("paused")]
        self.assertEqual(len(actifs), 20)
        for a in actifs:
            self.assertTrue(a.get("role_label"), f"{a['name']} sans rôle lisible")
        sorties = []
        for nom in NOUVEAUX:
            e = bras[nom]
            self.assertEqual(e["role"], "test")
            self.assertIs(e["learning"], False)
            self.assertEqual(e["capital_pct"], 0.0)
            self.assertIs(e["social_wishlist"], False)
            o = e["overrides"]
            self.assertEqual(o["risk_per_trade"], 0.01)
            self.assertEqual(o["risk_rules.max_concurrent_positions"], 3)
            self.assertIs(o["entry_rules"]["taille_fixe"], True)
            self.assertEqual(o["entry_rules"]["famille"], nom.split("_", 1)[1])
            sorties.append(o["exit_rules"])
            # Aucune dépendance Helius : ni porte anti_rug on-chain, ni copy_smart.
            texte = json.dumps(o)
            for interdit in ("anti_rug", "onchain_antirug", "copy_smart"):
                self.assertNotIn(interdit, texte, nom)
            # Aucun réglage mort.
            for cle in PARAMETRES_NON_APPLIQUES:
                section, champ = cle.split(".", 1)
                self.assertNotIn(champ, o.get(section, {}) or {}, f"{nom}: {cle}")
        self.assertTrue(all(s == sorties[0] for s in sorties), "sortie de référence identique")
        ref = sorties[0]
        self.assertEqual((ref["stop_loss_pct"], ref["take_profit_3"], ref["max_hold_time_minutes"]),
                         (-25.0, 30.0, 240))
        self.assertEqual(ref["breakeven_trigger"], 0)
        self.assertGreaterEqual(ref["trailing_stop_activation"], 1000)
        self.assertEqual(bras["entree_mature"]["discovery"], "mature")
        self.assertIs(bras["entree_score"]["overrides"]["entry_rules"]["sans_technique"], False)
        total = sum(float(a.get("capital_pct", 0)) for a in doc["arms"] if a.get("enabled", True))
        self.assertAlmostEqual(total, 1.0)

    def test_filtres_communs_dans_l_enveloppe(self):
        """Les nouveaux bras ne changent pas le lot que voient les autres."""
        from src.pipeline import discovery_envelope
        doc = _manifeste_production()
        anciens = []
        for a in doc["arms"]:
            if a["name"].startswith("entree_") or a.get("discovery") == "mature":
                continue
            chemin = os.path.join(ROOT, "config", "arms", f"{a['name']}.json")
            if a["name"] == "baseline":
                chemin = os.path.join(ROOT, "config", "params.json")
            with open(chemin, encoding="utf-8") as fh:
                anciens.append(json.load(fh)["filters"])
        avant = discovery_envelope(anciens)
        apres = discovery_envelope(anciens + [manifeste_script.FILTRES_COMMUNS])
        self.assertEqual(avant, apres)


class TestBrasEnPause(ArmsTestCase):
    def _bras(self):
        self._manifest([
            {"name": "baseline", "role": "voter", "capital_pct": 0.5, "notify": "all"},
            {"name": "sniper_young", "role": "voter", "capital_pct": 0.5, "paused": True,
             "role_label": "En pause · doublon"},
            manifeste_script.bras_familles(_mature_doc())[0],
            manifeste_script.bras_familles(_mature_doc())[1],
        ])
        return {a.name: a for a in bootstrap_arms()}

    def test_construit_mais_inerte(self):
        bras = self._bras()
        pause = bras["sniper_young"]
        self.assertTrue(pause.paused)
        self.assertIsNone(pause.learning)
        self.assertFalse(pause.is_voter)
        self.assertFalse(pause.social_wishlist)
        resume = pause.summary()
        self.assertIs(resume["paused"], True)
        self.assertEqual(resume["role_label"], "En pause · doublon")

    def test_famille_figee_et_lisible(self):
        bras = self._bras()
        h = bras["entree_hasard"]
        self.assertIsNone(h.learning)
        self.assertEqual(h.famille, "hasard")
        self.assertEqual(h.summary()["famille"], "hasard")
        self.assertIn("E0", h.summary()["role_label"])
        self.assertEqual(h.summary()["reglages_non_lus"], [])
        self.assertEqual(bras["entree_score"].entry_threshold, 55)
        self.assertEqual(h.entry_threshold, 0)
        self.assertEqual(h.max_positions, 3)
        # Le témoin porte encore des clés jamais lues : elles sont SIGNALÉES.
        base = bras["baseline"]
        base.params.set("entry_rules.entry_timing", "breakout_5m", log=False)
        self.assertIn("entry_rules.entry_timing", base.summary()["reglages_non_lus"])


class _Funnel:
    def __init__(self):
        self.rows = []

    def record(self, arm, token, symbol, gate, passed, reason="", extra=None):
        self.rows.append((arm, symbol, gate, passed, reason))


def _boucle(arms, evaluations):
    loop = object.__new__(main_module.AlphaLoop)
    loop.funnel = _Funnel()
    loop.arms = arms
    loop._last_evaluations = evaluations
    loop.mode = "PAPER"
    loop.params = SimpleNamespace(get=lambda k, d=None: d)
    loop._fleet_exposure = lambda: None
    loop.learning = None
    loop.ouvertes = []
    loop._open_position = lambda c, arm: loop.ouvertes.append((arm.name, c.symbol))
    loop._technical_verdict = mock.Mock(side_effect=AssertionError("technique appelée"))
    return loop


def _bras_stub(nom, regles, seuil=0, paused=False):
    params = {"entry_rules": regles, "scan.alpha_score_entry_threshold": seuil}
    return SimpleNamespace(
        name=nom, paused=paused, entry_threshold=seuil, max_positions=3, label=f" [{nom}]",
        role="test", portfolio=SimpleNamespace(capital=500.0),
        params=SimpleNamespace(get=lambda k, d=None: params.get(k, d)),
    )


class TestTryEntries(unittest.TestCase):
    def test_porte_de_famille_et_sans_technique(self):
        bon = cand("BON", **SUR)
        mauvais = cand("MAUVAIS", **{**SUR, "rugcheck_score": 10.0})
        bras = _bras_stub("entree_securite", {"famille": "securite", "sans_technique": True})
        loop = _boucle([bras], {})
        with mock.patch.object(main_module, "pre_trade_check", return_value=None):
            loop._try_entries(_evaluation(mauvais, bon), bras, {})
        self.assertEqual(loop.ouvertes, [("entree_securite", "BON")])
        portes = [(r[1], r[2], r[3]) for r in loop.funnel.rows if r[2] == "famille"]
        self.assertIn(("MAUVAIS", "famille", False), portes)
        self.assertIn(("BON", "famille", True), portes)
        loop._technical_verdict.assert_not_called()

    def test_bras_en_pause_n_entre_jamais(self):
        bras = _bras_stub("sniper_young", {}, paused=True)
        loop = _boucle([bras], {})
        loop._try_entries(_evaluation(cand("X")), bras, {})
        self.assertEqual(loop.ouvertes, [])
        self.assertEqual(loop.funnel.rows, [])

    def test_consensus_lit_les_familles(self):
        c = cand("C", alpha=60, **SUR, smart_money_wallets_30m=3, smart_money_buys_30m=3)
        score = _bras_stub("entree_score", {"famille": "score"}, seuil=55)
        secu = _bras_stub("entree_securite", {"famille": "securite"})
        cons = _bras_stub("entree_consensus", {
            "famille": "consensus", "sans_technique": True,
            "famille_reglages": {"membres": ["entree_score", "entree_securite"], "min_accords": 2}})
        evals = {"entree_score": _evaluation(c), "entree_securite": _evaluation(c)}
        loop = _boucle([score, secu, cons], evals)
        with mock.patch.object(main_module, "pre_trade_check", return_value=None):
            loop._try_entries(_evaluation(c), cons, {})
        self.assertEqual(loop.ouvertes, [("entree_consensus", "C")])
        # Un membre en pause ne compte plus : le quorum n'est plus atteint.
        secu.paused = True
        loop2 = _boucle([score, secu, cons], evals)
        with mock.patch.object(main_module, "pre_trade_check", return_value=None):
            loop2._try_entries(_evaluation(c), cons, {})
        self.assertEqual(loop2.ouvertes, [])


class TestMiseFixe(unittest.TestCase):
    def test_taille_fixe_ignore_kelly(self):
        portfolio = SimpleNamespace(
            capital=500.0,
            stats=lambda: {"win_rate": 90.0, "total_trades": 100},
            position_size=mock.Mock(side_effect=AssertionError("Kelly utilisé")),
        )
        params = {"entry_rules.taille_fixe": True, "risk_per_trade": 0.01}
        arm = SimpleNamespace(name="entree_x", portfolio=portfolio, max_positions=3, label="",
                              params=SimpleNamespace(get=lambda k, d=None: params.get(k, d)))
        loop = object.__new__(main_module.AlphaLoop)
        vu = {}

        def garde(candidate, size, arm):
            vu["size"] = size
            return 0.0, {"reason": "arrêt du test"}

        loop._economics_guard = garde
        loop.funnel = _Funnel()
        loop.jupiter = None
        loop.mode = "PAPER"
        loop.learning = None
        loop.params = SimpleNamespace(get=lambda k, d=None: d)
        loop._fleet_exposure = lambda: None
        with mock.patch.object(main_module, "pre_trade_check", return_value="refus test"):
            loop._open_position(cand("Z"), arm)
        self.assertEqual(vu["size"], 5.0)


class TestSuiviApresSortie(unittest.TestCase):
    def _journal(self, horizon, now=1000.0):
        cfg = dict(RESEARCH_DEFAULTS, enabled=True, post_exit_horizon_seconds=horizon)
        horloge = {"t": now}
        j = ResearchJournal(tempfile.mkdtemp(), cfg, clock=lambda: horloge["t"],
                            free_bytes=lambda: 10**12)
        return j, horloge

    def test_desactive_par_defaut(self):
        self.assertEqual(RESEARCH_DEFAULTS["post_exit_horizon_seconds"], 0)
        j, _ = self._journal(0)
        j.note_exited("TOK")
        self.assertNotIn("TOK", j._tracks)

    def test_prolonge_six_heures_et_prioritaire(self):
        j, horloge = self._journal(6 * 3600)
        j.note_exited("TOK")
        self.assertEqual(j._tracks["TOK"]["until"], 1000.0 + 6 * 3600)
        appels = []
        j.fetch = lambda batch: appels.append(list(batch)) or {t: (1.0, 2.0) for t in batch}
        self.assertEqual(j.tick(), 1)
        self.assertEqual(appels, [["TOK"]])
        # Une nouvelle entrée sur le même token ne raccourcit pas le suivi.
        j._register("TOK", 1100.0, None, None, "entered")
        self.assertEqual(j._tracks["TOK"]["until"], 1000.0 + 6 * 3600)

    def test_config_production(self):
        with open(os.path.join(ROOT, "config", "research_log.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)
        self.assertEqual(cfg.get("post_exit_horizon_seconds"), 21600)
        self.assertLessEqual(cfg.get("max_calls_per_min"), 6)


if __name__ == "__main__":
    unittest.main()


class TestScanSansLesBrasEnPause(unittest.TestCase):
    def test_pause_hors_evaluation_mais_dans_l_enveloppe(self):
        from src.pipeline import CollectedBatch, discovery_envelope
        from tests.test_new_arms import _arm, _cand as _c

        batch = CollectedBatch(enriched=(_c("A"),))
        envelopes, evalues = [], []

        def evaluate(b, params=None, label="", social=None, verbose=False):
            evalues.append(label)
            addr = {c.token_address for c in b.enriched}
            return SimpleNamespace(
                result=SimpleNamespace(candidates=b.enriched),
                kept_addresses=frozenset(addr), rejected_addresses=frozenset(),
                wishlist=tuple(addr))

        pipeline = SimpleNamespace(
            collect=lambda env: envelopes.append(env) or batch,
            collect_mature=mock.Mock(), evaluate=evaluate,
            enrich_social=lambda enriched, addresses: {},
            apply_cache_effects=lambda kept, rejected, bl: None,
        )
        base = _arm("baseline", {"max_age_hours": 16.5, "min_liquidity_usd": 80000}, baseline=True)
        pause = _arm("sniper_young", {"max_age_hours": 20, "min_liquidity_usd": 10000})
        pause.paused = True
        fake = SimpleNamespace(
            arms=[base, pause], pipeline=pipeline, params={},
            _observe_wallets=lambda b: None, quota_agent=mock.Mock(),
            voters={"baseline"}, baseline=base, _print_arms=lambda e, p: None,
        )
        with mock.patch.object(main_module, "build_confluence", return_value={}):
            main_module.AlphaLoop._scan_all_arms(fake)
        # L'enveloppe garde le bras en pause : le lot des autres ne change pas.
        self.assertEqual(envelopes[0], discovery_envelope([base.filters(), pause.filters()]))
        self.assertNotIn("[sniper_young]", evalues)
        self.assertIn("[baseline]", evalues)
        self.assertNotIn("sniper_young", fake._last_evaluations)


class TestNettoyageReglagesMorts:
    def _base(self, tmp_path):
        (tmp_path / "config" / "arms").mkdir(parents=True)
        (tmp_path / "data").mkdir()
        doc = {"entry_rules": {"entry_timing": "breakout_5m", "min_alpha_score": 60},
               "filters": {"max_tax_buy": 5, "min_liquidity_usd": 1}}
        (tmp_path / "config" / "params.json").write_text(json.dumps(doc))
        (tmp_path / "config" / "arms" / "x.json").write_text(json.dumps(doc))
        return tmp_path

    def test_liste_sans_ecrire(self, tmp_path, capsys):
        from scripts import nettoyer_reglages_morts as n
        base = self._base(tmp_path)
        avant = (base / "config" / "params.json").read_text()
        assert n.main([], base=str(base)) == 0
        assert (base / "config" / "params.json").read_text() == avant
        assert "entry_rules.entry_timing" in capsys.readouterr().out

    def test_appliquer_retire_seulement_les_cles_mortes(self, tmp_path):
        from scripts import nettoyer_reglages_morts as n
        base = self._base(tmp_path)
        assert n.main(["--appliquer"], base=str(base)) == 0
        doc = json.loads((base / "config" / "arms" / "x.json").read_text())
        assert doc == {"entry_rules": {"min_alpha_score": 60}, "filters": {"min_liquidity_usd": 1}}

    def test_refuse_si_bot_en_marche(self, tmp_path):
        import os
        from scripts import nettoyer_reglages_morts as n
        base = self._base(tmp_path)
        (base / "data" / "alpha_loop.pid").write_text(str(os.getpid()))
        avant = (base / "config" / "params.json").read_text()
        assert n.main(["--appliquer"], base=str(base)) == 2
        assert (base / "config" / "params.json").read_text() == avant

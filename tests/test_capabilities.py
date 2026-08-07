"""Chaînes de repli entre fournisseurs — dégradation annoncée, jamais subie.

Le problème réel verrouillé ici : Twitter et Birdeye sont morts en cours de
route (402, quota CU épuisé) et le bot a continué à les appeler en silence.
Ces tests vérifient que `Capability.resolve` bascule sur le fournisseur
suivant sans planter, qu'un résultat vide n'est PAS confondu avec une panne,
et que l'absence totale de fournisseur vivant ("blind spot") est rapportée.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.capabilities import (  # noqa: E402
    CANDLES,
    Capability,
    CapabilityRegistry,
    HOLDERS,
    PRICE,
    Provider,
    build_registry,
)


def _provider(name, available=True, result="ok", raises=None, cost=1, free=True):
    def _available():
        return available

    def _call(*args, **kwargs):
        if raises is not None:
            raise raises
        return result

    return Provider(name=name, available=_available, call=_call, cost=cost, free=free)


class TestCapabilityResolve(unittest.TestCase):
    def test_premier_fournisseur_disponible_est_utilise(self):
        capability = Capability(name="test", providers=[_provider("a"), _provider("b")])
        result, used = capability.resolve()
        self.assertEqual(result, "ok")
        self.assertEqual(used, "a")
        self.assertFalse(capability.degraded)

    def test_bascule_sur_le_suivant_si_le_premier_est_indisponible(self):
        capability = Capability(
            name="test",
            providers=[_provider("a", available=False), _provider("b")],
        )
        result, used = capability.resolve()
        self.assertEqual(used, "b")
        self.assertTrue(capability.degraded)

    def test_bascule_sur_le_suivant_si_le_premier_leve_une_exception(self):
        # Le bot a brûlé 489 requêtes sur un quota mort : une source qui
        # explose ne doit jamais interrompre la chaîne de repli.
        capability = Capability(
            name="test",
            providers=[_provider("a", raises=RuntimeError("402")), _provider("b")],
        )
        result, used = capability.resolve()
        self.assertEqual(result, "ok")
        self.assertEqual(used, "b")

    def test_resultat_vide_nest_pas_une_panne_mais_passe_au_suivant(self):
        # Un token peut simplement ne pas avoir de bougies : le fournisseur
        # n'est pas marqué mort pour autant, on essaie juste le suivant.
        capability = Capability(
            name="test",
            providers=[_provider("a", result=None), _provider("b", result="ok")],
        )
        result, used = capability.resolve()
        self.assertEqual(result, "ok")
        self.assertEqual(used, "b")

    def test_resultat_vide_liste_bascule_aussi(self):
        capability = Capability(
            name="test",
            providers=[_provider("a", result=[]), _provider("b", result=["x"])],
        )
        result, used = capability.resolve()
        self.assertEqual(result, ["x"])
        self.assertEqual(used, "b")

    def test_aucun_fournisseur_disponible_rend_none_none(self):
        capability = Capability(
            name="test",
            providers=[_provider("a", available=False), _provider("b", available=False)],
        )
        result, used = capability.resolve()
        self.assertIsNone(result)
        self.assertIsNone(used)
        self.assertTrue(capability.degraded)
        self.assertIsNone(capability.last_used)

    def test_aucun_fournisseur_du_tout(self):
        capability = Capability(name="vide", providers=[])
        result, used = capability.resolve()
        self.assertIsNone(result)
        self.assertIsNone(used)

    def test_args_et_kwargs_transmis_au_fournisseur(self):
        captured = {}

        def _call(addr, window=30):
            captured["addr"] = addr
            captured["window"] = window
            return "ok"

        capability = Capability(
            name="test",
            providers=[Provider("a", available=lambda: True, call=_call)],
        )
        capability.resolve("addr123", window=60)
        self.assertEqual(captured, {"addr": "addr123", "window": 60})


class TestCapabilityStatus(unittest.TestCase):
    def test_status_liste_vivants_et_morts(self):
        capability = Capability(
            name="bougies",
            providers=[_provider("a", available=False), _provider("b", available=True)],
        )
        capability.resolve()
        status = capability.status
        self.assertEqual(status["available"], ["b"])
        self.assertEqual(status["down"], ["a"])
        self.assertFalse(status["blind"])

    def test_blind_quand_aucun_fournisseur_vivant(self):
        capability = Capability(
            name="bougies", providers=[_provider("a", available=False)]
        )
        self.assertTrue(capability.status["blind"])

    def test_available_qui_leve_est_traite_comme_mort(self):
        def _boom():
            raise RuntimeError("timeout")

        capability = Capability(
            name="bougies",
            providers=[Provider("a", available=_boom, call=lambda: "ok")],
        )
        status = capability.status
        self.assertEqual(status["down"], ["a"])
        self.assertTrue(status["blind"])


class TestCapabilityRegistry(unittest.TestCase):
    def test_register_puis_resolve_par_nom(self):
        registry = CapabilityRegistry()
        registry.register("prix", [_provider("jupiter")])
        result, used = registry.resolve("prix")
        self.assertEqual(result, "ok")
        self.assertEqual(used, "jupiter")

    def test_resolve_capacite_inconnue_ne_leve_pas(self):
        registry = CapabilityRegistry()
        result, used = registry.resolve("jamais_enregistree")
        self.assertIsNone(result)
        self.assertIsNone(used)

    def test_report_agrege_toutes_les_capacites(self):
        registry = CapabilityRegistry()
        registry.register("prix", [_provider("jupiter")])
        registry.register("holders", [_provider("birdeye", available=False)])
        noms = [entry["capability"] for entry in registry.report]
        self.assertEqual(sorted(noms), ["holders", "prix"])

    def test_blind_spots_ne_liste_que_les_capacites_sans_source(self):
        registry = CapabilityRegistry()
        registry.register("prix", [_provider("jupiter")])
        registry.register("social", [_provider("twitter", available=False)])
        self.assertEqual(registry.blind_spots, ["social"])

    def test_summary_marque_trou_degradation_et_sante(self):
        registry = CapabilityRegistry()
        registry.register("sain", [_provider("a")])
        registry.register("degrade", [_provider("a", available=False), _provider("b")])
        registry.register("aveugle", [_provider("a", available=False)])
        resume = registry.summary()
        self.assertIn("✓ sain", resume)
        self.assertIn("~ degrade", resume)
        self.assertIn("✗ aveugle", resume)


class FakeSource:
    def __init__(self, enabled=True):
        self.enabled = enabled

    def kline(self, addr, **kw):
        return [{"c": 1.0}]

    def get_ohlcv(self, addr, **kw):
        return [{"c": 1.0}]

    def get_overview(self, addr):
        return {"holders": 100}

    def get_holder_stats(self, addr, **kw):
        return {"holders": 90}

    def prices(self, mints):
        return {m: 1.0 for m in mints}


class FakeDex:
    def get_tokens_data(self, mints):
        return {m: 1.0 for m in mints}


class TestBuildRegistry(unittest.TestCase):
    def test_gmgn_precede_birdeye_pour_les_bougies(self):
        # GMGN gratuit sans plafond mensuel passe AVANT Birdeye payant/plafonné
        # même si Birdeye est la source "officielle".
        registry = build_registry(gmgn=FakeSource(), birdeye=FakeSource())
        _, used = registry.resolve(CANDLES, "addr")
        self.assertEqual(used, "gmgn/kline")

    def test_birdeye_precede_helius_pour_les_holders(self):
        # Birdeye rend un compte EXACT, Helius une borne inférieure.
        registry = build_registry(birdeye=FakeSource(), helius=FakeSource())
        _, used = registry.resolve(HOLDERS, "addr")
        self.assertEqual(used, "birdeye/overview")

    def test_repli_effectif_quand_la_source_preferee_est_absente(self):
        registry = build_registry(birdeye=None, helius=FakeSource())
        _, used = registry.resolve(HOLDERS, "addr")
        self.assertEqual(used, "helius")

    def test_jupiter_precede_dexscreener_pour_le_prix(self):
        registry = build_registry(jupiter=FakeSource(), dex=FakeDex())
        _, used = registry.resolve(PRICE, ["mintA"])
        self.assertEqual(used, "jupiter/price")

    def test_aucune_source_configuree_rend_tout_aveugle(self):
        registry = build_registry()
        self.assertIn(CANDLES, registry.blind_spots)
        self.assertIn(HOLDERS, registry.blind_spots)
        self.assertIn(PRICE, registry.blind_spots)


if __name__ == "__main__":
    unittest.main(verbosity=2)

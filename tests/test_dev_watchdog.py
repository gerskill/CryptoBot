"""Tests du gardien anti-slow-rug — src/core/dev_watchdog.py."""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.dev_watchdog import DevWatchdog  # noqa: E402

CREATEUR = "CreatorWallet1111111111111111111111111111111"
AUTRE = "OtherWallet22222222222222222222222222222222"
TOKEN = "Mint1111111111111111111111111111111111111111"


class FakeHelius:
    """Rend des tops de holders scriptés, un par appel successif."""

    def __init__(self, creator=CREATEUR, tops=None, raises_on=()):
        self._creator = creator
        self._tops = list(tops or [])
        self._raises_on = set(raises_on)
        self.creator_calls = 0
        self.holder_calls = 0

    def get_creator_address(self, mint):
        self.creator_calls += 1
        if "creator" in self._raises_on:
            raise RuntimeError("RPC indisponible")
        return self._creator

    def get_top_holders(self, mint, supply=None):
        self.holder_calls += 1
        if "holders" in self._raises_on:
            raise RuntimeError("RPC indisponible")
        if not self._tops:
            return []
        return self._tops.pop(0) if len(self._tops) > 1 else self._tops[0]


def top(*pairs):
    return [{"address": a, "amount": 0.0, "pct": p} for a, p in pairs]


class TestSnapshot(unittest.TestCase):
    def test_createur_visible(self):
        helius = FakeHelius(tops=[top((CREATEUR, 8.0), (AUTRE, 3.0))])
        snap = DevWatchdog(helius).snapshot(TOKEN)
        self.assertTrue(snap.visible)
        self.assertEqual(snap.pct, 8.0)
        self.assertEqual(snap.floor_pct, 3.0)

    def test_createur_absent_du_top_nest_pas_zero(self):
        """Le piège que `get_dev_wallet_pct` ne distingue pas."""
        helius = FakeHelius(tops=[top((AUTRE, 3.0))])
        snap = DevWatchdog(helius).snapshot(TOKEN)
        self.assertFalse(snap.visible)
        self.assertIsNone(snap.pct)
        self.assertFalse(snap.known)

    def test_createur_inconnu(self):
        snap = DevWatchdog(FakeHelius(creator=None)).snapshot(TOKEN)
        self.assertIsNone(snap.creator)
        self.assertFalse(snap.known)

    def test_adresse_du_createur_mise_en_cache(self):
        helius = FakeHelius(tops=[top((CREATEUR, 8.0))])
        watchdog = DevWatchdog(helius)
        for _ in range(4):
            watchdog.snapshot(TOKEN)
        self.assertEqual(helius.creator_calls, 1)
        self.assertEqual(helius.holder_calls, 4)

    def test_panne_rpc_rend_non_mesure_sans_lever(self):
        for panne in ("creator", "holders"):
            snap = DevWatchdog(FakeHelius(tops=[top((CREATEUR, 8.0))],
                                          raises_on=(panne,))).snapshot(TOKEN)
            self.assertFalse(snap.known, panne)


class TestVerdict(unittest.TestCase):
    def watchdog(self, tops, **kwargs):
        return DevWatchdog(FakeHelius(tops=tops), interval_seconds=0, **kwargs)

    def test_baseline_puis_vente_massive_declenche(self):
        # 8 % -> 4 % : -4 points, -50 % de sa mise. Les deux seuils tombent.
        watchdog = self.watchdog([
            top((CREATEUR, 8.0), (AUTRE, 3.0)),
            top((CREATEUR, 4.0), (AUTRE, 3.0)),
        ])
        watchdog.establish_baseline(TOKEN)
        verdict = watchdog.check(TOKEN, force=True)
        self.assertTrue(verdict.dumping)
        self.assertAlmostEqual(verdict.drop_pts, 4.0)
        self.assertAlmostEqual(verdict.drop_relative, 0.5)
        self.assertTrue(watchdog.already_flagged(TOKEN))

    def test_baisse_relative_forte_mais_absolue_minuscule_ne_declenche_pas(self):
        """0,9 % -> 0,4 % : -56 % relatif, mais 0,5 point. Bruit, pas un dump.

        Les deux conditions sont CUMULATIVES, c'est tout l'objet du test.
        """
        watchdog = self.watchdog([
            top((CREATEUR, 0.9), (AUTRE, 0.2)),
            top((CREATEUR, 0.4), (AUTRE, 0.2)),
        ], min_baseline_pct=0.5)
        watchdog.establish_baseline(TOKEN)
        verdict = watchdog.check(TOKEN, force=True)
        self.assertFalse(verdict.dumping)
        self.assertGreater(verdict.drop_relative, 0.5)

    def test_baisse_absolue_forte_mais_relative_faible_ne_declenche_pas(self):
        """40 % -> 38,5 % : 1,5 point cédé, mais 3,75 % de sa mise."""
        watchdog = self.watchdog([
            top((CREATEUR, 40.0), (AUTRE, 3.0)),
            top((CREATEUR, 38.5), (AUTRE, 3.0)),
        ])
        watchdog.establish_baseline(TOKEN)
        self.assertFalse(watchdog.check(TOKEN, force=True).dumping)

    def test_createur_sorti_du_top_donne_une_borne_inferieure(self):
        """Disparu du top 20 : on sait « au moins », pas « exactement »."""
        watchdog = self.watchdog([
            top((CREATEUR, 9.0), (AUTRE, 3.0)),
            top((AUTRE, 3.0), ("W3", 1.0)),
        ])
        watchdog.establish_baseline(TOKEN)
        verdict = watchdog.check(TOKEN, force=True)
        self.assertTrue(verdict.dumping)
        self.assertTrue(verdict.is_lower_bound)
        # Borné par le plus petit compte encore listé (1,0), pas par 0.
        self.assertEqual(verdict.current_pct, 1.0)
        self.assertIn("au moins", verdict.reason)

    def test_createur_invisible_a_l_ouverture_ne_declenche_jamais(self):
        watchdog = self.watchdog([
            top((AUTRE, 3.0)),
            top((AUTRE, 3.0)),
        ])
        watchdog.establish_baseline(TOKEN)
        verdict = watchdog.check(TOKEN, force=True)
        self.assertFalse(verdict.dumping)
        self.assertIn("non mesurable", verdict.reason)

    def test_petite_mise_ignoree(self):
        watchdog = self.watchdog([top((CREATEUR, 0.2), (AUTRE, 0.1))])
        watchdog.establish_baseline(TOKEN)
        verdict = watchdog.check(TOKEN, force=True)
        self.assertFalse(verdict.dumping)
        self.assertIn("rien à distribuer", verdict.reason)

    def test_hausse_du_solde_ne_declenche_pas(self):
        watchdog = self.watchdog([
            top((CREATEUR, 5.0), (AUTRE, 3.0)),
            top((CREATEUR, 6.0), (AUTRE, 3.0)),
        ])
        watchdog.establish_baseline(TOKEN)
        self.assertFalse(watchdog.check(TOKEN, force=True).dumping)


class TestCadenceEtMemoire(unittest.TestCase):
    def test_cadence_respectee(self):
        helius = FakeHelius(tops=[top((CREATEUR, 8.0), (AUTRE, 3.0))])
        watchdog = DevWatchdog(helius, interval_seconds=10_000)
        watchdog.establish_baseline(TOKEN)
        appels = helius.holder_calls
        for _ in range(5):
            self.assertFalse(watchdog.check(TOKEN).dumping)
        self.assertEqual(helius.holder_calls, appels, "aucun appel avant l'échéance")

    def test_baseline_idempotente_le_premier_gagne(self):
        """Sept bras peuvent ouvrir sur le même token à des instants différents."""
        watchdog = DevWatchdog(FakeHelius(tops=[
            top((CREATEUR, 9.0), (AUTRE, 3.0)),
            top((CREATEUR, 2.0), (AUTRE, 3.0)),
        ]), interval_seconds=0)
        premiere = watchdog.establish_baseline(TOKEN)
        deuxieme = watchdog.establish_baseline(TOKEN)
        self.assertEqual(premiere.pct, 9.0)
        self.assertIs(premiere, deuxieme)

    def test_forget_libere_la_memoire(self):
        watchdog = DevWatchdog(FakeHelius(tops=[top((CREATEUR, 8.0))]), interval_seconds=0)
        watchdog.establish_baseline(TOKEN)
        watchdog.check(TOKEN, force=True)
        self.assertIn(TOKEN, watchdog.tracked_tokens())
        watchdog.forget(TOKEN)
        self.assertEqual(watchdog.tracked_tokens(), ())
        self.assertFalse(watchdog.already_flagged(TOKEN))

    def test_unflag_permet_un_nouveau_controle(self):
        """Fermeture reportée faute de prix : le token doit rester surveillé."""
        watchdog = DevWatchdog(FakeHelius(tops=[
            top((CREATEUR, 8.0), (AUTRE, 3.0)),
            top((CREATEUR, 1.0), (AUTRE, 3.0)),
        ]), interval_seconds=0)
        watchdog.establish_baseline(TOKEN)
        self.assertTrue(watchdog.check(TOKEN, force=True).dumping)
        watchdog.unflag(TOKEN)
        self.assertFalse(watchdog.already_flagged(TOKEN))
        self.assertTrue(watchdog.check(TOKEN, force=True).dumping)


class FakeJournal:
    def read_positions(self):
        return []

    def record_exit(self, position, exit_price, pnl_pct, fraction, reason,
                    is_final, **_):
        return {
            "pnl_pct": pnl_pct,
            "pnl_usd": position.realized_pnl_usd,
            "exit_reason": reason,
            "is_final_exit": is_final,
        }


class FakeTelegram:
    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


class FakeFunnel:
    def __init__(self):
        self.exits = []

    def record_exit(self, arm, position, price, reason, pnl_pct, drop=None):
        self.exits.append((arm, position.symbol, reason))


class TestCablageDansLaBoucle(unittest.TestCase):
    """Le chemin qui DÉCIDE : détection -> fermeture sur tous les bras."""

    def build(self, tops, panic=True, price=2.0):
        import json
        import tempfile

        from src.core.models import Candidate
        from src.core.params import ParamsStore
        from src.core.portfolio import PaperPortfolio
        from src.core.pricefeed import CycleMarketCache
        from src.main import AlphaLoop

        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        )
        json.dump({"risk_rules": {"dev_dump_panic_exit": panic}}, handle)
        handle.close()
        self.addCleanup(os.unlink, handle.name)

        candidate = Candidate(
            token_address=TOKEN, symbol="TOAD", name="Toad",
            chain="solana", price_usd=1.0,
        )

        class Arm:
            def __init__(self, name):
                self.name = name
                self.label = f" [{name}]"
                self.portfolio = PaperPortfolio(capital=1000.0, journal=FakeJournal())
                self.portfolio.open(candidate, {"exit_rules": {}}, 30.0)

        loop = object.__new__(AlphaLoop)
        loop.arms = [Arm("sniper"), Arm("runner")]
        loop.params = ParamsStore(handle.name)
        loop.telegram = FakeTelegram()
        loop.funnel = FakeFunnel()
        loop.dev_watchdog = DevWatchdog(FakeHelius(tops=tops), interval_seconds=0)
        loop.market = CycleMarketCache()
        loop.market.put("solana", None, TOKEN, price, None)
        loop._measure_hold = lambda *a, **k: None
        loop._after_trade_closed = lambda *a, **k: None
        return loop

    def tops_dump(self):
        return [
            top((CREATEUR, 9.0), (AUTRE, 3.0)),
            top((CREATEUR, 2.0), (AUTRE, 3.0)),
        ]

    def test_dump_ferme_les_positions_de_tous_les_bras(self):
        loop = self.build(self.tops_dump())
        loop.dev_watchdog.establish_baseline(TOKEN)
        self.assertEqual(sum(len(a.portfolio.positions) for a in loop.arms), 2)

        loop._watch_dev_dumps()

        self.assertEqual(
            sum(len(a.portfolio.positions) for a in loop.arms), 0,
            "les deux bras doivent être sortis, pas seulement celui qui a vu",
        )
        self.assertEqual([r for _, _, r in loop.funnel.exits], ["DEV_DUMP"] * 2)
        self.assertTrue(loop.telegram.sent)

    def test_mode_observation_alerte_sans_fermer(self):
        loop = self.build(self.tops_dump(), panic=False)
        loop.dev_watchdog.establish_baseline(TOKEN)
        loop._watch_dev_dumps()
        self.assertEqual(sum(len(a.portfolio.positions) for a in loop.arms), 2)
        self.assertTrue(loop.telegram.sent)
        self.assertIn("Observation seule", loop.telegram.sent[0])

    def test_pas_de_dump_ne_touche_a_rien(self):
        loop = self.build([top((CREATEUR, 9.0), (AUTRE, 3.0))])
        loop.dev_watchdog.establish_baseline(TOKEN)
        loop._watch_dev_dumps()
        self.assertEqual(sum(len(a.portfolio.positions) for a in loop.arms), 2)
        self.assertEqual(loop.telegram.sent, [])

    def test_prix_indisponible_reporte_la_fermeture_sans_perdre_l_alerte(self):
        """Fermer sans prix inventerait un P&L ; le token doit rester surveillé."""
        loop = self.build(self.tops_dump())
        loop.dev_watchdog.establish_baseline(TOKEN)
        loop.market.reset()  # plus aucun prix connu

        loop._watch_dev_dumps()

        self.assertEqual(sum(len(a.portfolio.positions) for a in loop.arms), 2)
        self.assertFalse(
            loop.dev_watchdog.already_flagged(TOKEN),
            "le signalement doit être annulé pour que le prochain tick réessaie",
        )

    def test_token_sans_position_est_oublie(self):
        loop = self.build([top((CREATEUR, 9.0), (AUTRE, 3.0))])
        loop.dev_watchdog.establish_baseline("MintOrphelin")
        loop._watch_dev_dumps()
        self.assertNotIn("MintOrphelin", loop.dev_watchdog.tracked_tokens())


if __name__ == "__main__":
    unittest.main()

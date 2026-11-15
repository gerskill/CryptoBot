"""Résolution de paire des positions entrées sans `pair_address` (GMGN)."""

import os
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.analysis.technical import PriceHistory  # noqa: E402
from src.core.pair_resolver import PairResolver, pick_best_pair  # noqa: E402
from src.core.portfolio import PaperPortfolio as Portfolio  # noqa: E402
from src.core.positions import Position  # noqa: E402
from src.main import AlphaLoop  # noqa: E402

MINT = "DzMwArmdzPqcqgvyqAmTxy79XdA9NwK9mMteqWLJpump"


def pair(address, dex, liq=None, price="0.001", chain="solana"):
    p = {"pairAddress": address, "dexId": dex, "chainId": chain, "priceUsd": price,
         "baseToken": {"address": MINT}}
    if liq is not None:
        p["liquidity"] = {"usd": liq}
    return p


NORMIE_PAIRS = [
    pair("PF1", "pumpfun"), pair("PF2", "pumpfun", price=None),
    pair("PSWAP", "pumpswap", 104000), pair("MET1", "meteora", 3500),
    pair("ETH", "uniswap", 900000, chain="ethereum"),
]


class FakeFetch:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def __call__(self, tokens):
        self.calls.append(list(tokens))
        return {t: self.data.get(t, []) for t in tokens}


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class TestPickBestPair(unittest.TestCase):
    def test_paire_migree_a_liquidite_max(self):
        self.assertEqual(pick_best_pair(NORMIE_PAIRS, "solana")["pairAddress"], "PSWAP")

    def test_pumpfun_evitee_meme_si_seule_avec_liquidite_haute(self):
        pairs = [pair("PF", "pumpfun", 500000), pair("RAY", "raydium", 20000)]
        self.assertEqual(pick_best_pair(pairs, "solana")["pairAddress"], "RAY")

    def test_pumpfun_seule_retenue_a_defaut(self):
        self.assertEqual(pick_best_pair([pair("PF1", "pumpfun")], "solana")["pairAddress"], "PF1")

    def test_mauvaise_chaine_ou_sans_prix_ignorees(self):
        self.assertIsNone(pick_best_pair([pair("ETH", "uniswap", 1e6, chain="ethereum"),
                                          pair("X", "raydium", 1e5, price=None)], "solana"))


class TestResolverCache(unittest.TestCase):
    def test_resout_les_positions_sans_paire_en_un_appel_groupe(self):
        others = {f"T{i}": [dict(pair(f"P{i}", "raydium", 10000), baseToken={"address": f"T{i}"})]
                  for i in range(5)}
        fetch = FakeFetch({MINT: NORMIE_PAIRS, **others})
        r = PairResolver(fetch, clock=Clock())
        wanted = [(MINT, "solana", None)] + [(t, "solana", None) for t in others] + [("HAS", "solana", "P")]
        changes = r.resolve(wanted)
        self.assertEqual(len(fetch.calls), 1)
        self.assertNotIn("HAS", fetch.calls[0])
        self.assertEqual(changes[MINT], "PSWAP")
        self.assertEqual(len(changes), 6)

    def test_pas_de_nouvel_appel_avant_le_delai(self):
        clock = Clock()
        fetch = FakeFetch({})  # token pas encore indexé
        r = PairResolver(fetch, retry_seconds=120, clock=clock)
        self.assertEqual(r.resolve([(MINT, "solana", None)]), {})
        clock.t += 60
        r.resolve([(MINT, "solana", None)])
        self.assertEqual(len(fetch.calls), 1)
        clock.t += 61
        r.resolve([(MINT, "solana", None)])
        self.assertEqual(len(fetch.calls), 2)

    def test_lot_limite_a_30_tokens(self):
        fetch = FakeFetch({})
        r = PairResolver(fetch, clock=Clock())
        r.resolve([(f"T{i}", "solana", None) for i in range(45)])
        self.assertEqual(len(fetch.calls), 1)
        self.assertEqual(len(fetch.calls[0]), 30)

    def test_erreur_reseau_ne_leve_pas(self):
        def boom(tokens):
            raise TimeoutError("x")
        self.assertEqual(PairResolver(boom, clock=Clock()).resolve([(MINT, "solana", None)]), {})

    def test_migration_re_resolution_apres_perte_de_liquidite(self):
        clock = Clock()
        fetch = FakeFetch({MINT: [pair("PF1", "pumpfun")]})
        r = PairResolver(fetch, clock=clock)
        self.assertEqual(r.resolve([(MINT, "solana", None)]), {MINT: "PF1"})
        # La paire suivie n'a pas de liquidité → stale, re-tentée après délai.
        r.mark_stale(MINT)
        self.assertEqual(r.resolve([(MINT, "solana", "PF1")]), {})  # délai pas écoulé
        clock.t += 121
        fetch.data[MINT] = [pair("PF1", "pumpfun"), pair("PSWAP", "pumpswap", 80000)]
        self.assertEqual(r.resolve([(MINT, "solana", "PF1")]), {MINT: "PSWAP"})
        # Résolue avec liquidité : plus stale, plus d'appels.
        clock.t += 500
        r.resolve([(MINT, "solana", "PSWAP")])
        self.assertEqual(len(fetch.calls), 2)

    def test_paire_avec_liquidite_jamais_re_resolue(self):
        fetch = FakeFetch({MINT: NORMIE_PAIRS})
        r = PairResolver(fetch, clock=Clock())
        self.assertEqual(r.resolve([(MINT, "solana", "PSWAP")]), {})
        self.assertEqual(fetch.calls, [])


class TestPersistance(unittest.TestCase):
    def test_paire_resolue_persistee_et_rechargee(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "open_positions.json")
            from src.core.journal import TradeJournal
            journal = TradeJournal(os.path.join(d, "trades.jsonl"))
            pf = Portfolio(1000.0, journal, positions_path=path)
            pos = Position(token_address=MINT, symbol="Normie", chain="solana",
                           entry_price=0.00089, size_usd=20.0)
            pf.positions[pos.id] = pos
            pf.save_positions()
            self.assertTrue(pf.set_pair_address(pos.id, "PSWAP"))
            self.assertFalse(pf.set_pair_address(pos.id, "PSWAP"))
            reloaded = Portfolio(1000.0, journal, positions_path=path)
            p2 = reloaded.positions[pos.id]
            self.assertEqual(p2.pair_address, "PSWAP")
            self.assertIsNotNone(p2.pair_resolved_at)
            self.assertEqual(p2.entry_price, 0.00089)


class FakeDex:
    def __init__(self, pairs_by_address):
        self.pairs = pairs_by_address
        self.pair_calls = 0

    def get_pair_data(self, chain, address):
        self.pair_calls += 1
        p = self.pairs.get(address)
        return {"pairs": [p]} if p else {}


class TestMonitoringIntegration(unittest.TestCase):
    def _loop(self, positions, fetch, dex):
        portfolio = SimpleNamespace(positions={p.id: p for p in positions}, saved=0)

        def set_pair(pid, pa):
            from dataclasses import replace
            cur = portfolio.positions[pid]
            if cur.pair_address == pa:
                return False
            portfolio.positions[pid] = replace(cur, pair_address=pa, pair_resolved_at=time.time())
            return True
        portfolio.set_pair_address = set_pair
        loop = SimpleNamespace(
            arms=[SimpleNamespace(portfolio=portfolio)],
            pair_resolver=PairResolver(fetch, clock=Clock()),
            dex=dex, jupiter=SimpleNamespace(price=lambda m: 0.00103),
            birdeye=SimpleNamespace(get_price=lambda *a: None),
        )
        loop._mark_pair_stale = lambda t: AlphaLoop._mark_pair_stale(loop, t)
        loop._open_positions = lambda: list(portfolio.positions.values())
        return loop, portfolio

    def test_position_gmgn_lit_la_liquidite_de_la_paire_resolue(self):
        a = Position(token_address=MINT, symbol="Normie", chain="solana", entry_price=0.00089, size_usd=20)
        b = Position(token_address=MINT, symbol="Normie", chain="solana", entry_price=0.00089, size_usd=30)
        fetch = FakeFetch({MINT: NORMIE_PAIRS})
        dex = FakeDex({"PSWAP": pair("PSWAP", "pumpswap", 104000, price="0.00105")})
        loop, pf = self._loop([a, b], fetch, dex)
        # Avant : sans paire → Jupiter, liquidité inconnue.
        self.assertEqual(AlphaLoop._current_market(loop, a), (0.00103, None))
        self.assertEqual(AlphaLoop._resolve_pairs(loop, loop._open_positions()), 2)
        self.assertEqual(len(fetch.calls), 1)
        for p in pf.positions.values():
            self.assertEqual(p.pair_address, "PSWAP")
            self.assertEqual(AlphaLoop._current_market(loop, p), (0.00105, 104000.0))
        # Entrée inchangée.
        self.assertTrue(all(p.entry_price == 0.00089 for p in pf.positions.values()))

    def test_paire_sans_liquidite_marquee_stale_puis_migree(self):
        a = Position(token_address=MINT, symbol="Normie", chain="solana", entry_price=1.0,
                     size_usd=20, pair_address="PF1")
        fetch = FakeFetch({MINT: [pair("PF1", "pumpfun"), pair("PSWAP", "pumpswap", 90000)]})
        dex = FakeDex({"PF1": pair("PF1", "pumpfun"), "PSWAP": pair("PSWAP", "pumpswap", 90000)})
        loop, pf = self._loop([a], fetch, dex)
        price, liq = AlphaLoop._current_market(loop, a)
        self.assertIsNone(liq)
        self.assertTrue(loop.pair_resolver.needs(MINT, "PF1"))
        AlphaLoop._resolve_pairs(loop, loop._open_positions())
        self.assertEqual(pf.positions[a.id].pair_address, "PSWAP")

    def test_changement_de_paire_ne_cree_pas_de_faux_rug(self):
        """Ancienne paire 200 k, nouvelle 60 k : sources distinctes, pas de -70 %."""
        h = PriceHistory()
        now = time.time()
        for i in range(3):
            h.record_raw(MINT, price=1.0, liquidity=200000, ts=now - 50 + 5 * i, source="monitor:OLD")
        for i in range(3):
            h.record_raw(MINT, price=1.0, liquidity=60000, ts=now - 10 + 5 * i, source="monitor:NEW")
        self.assertEqual(h.liquidity_drop_pct(MINT, 120, source="monitor:NEW", confirm_readings=2), 0.0)


if __name__ == "__main__":
    unittest.main()

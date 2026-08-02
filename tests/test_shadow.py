"""Tests du suivi des rejets, des bornes et du backtest de validation."""

import json
import os
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.journal import TradeJournal  # noqa: E402
from src.core.learning import INACTIVITY_CYCLES, LearningEngine  # noqa: E402
from src.core.models import Candidate  # noqa: E402
from src.core.params import ParamsStore  # noqa: E402
from src.core.shadow import ShadowTracker, reason_family  # noqa: E402

PARAMS = {
    "version": "2.0",
    "risk_per_trade": 0.03,
    "filters": {
        "min_age_hours": 0.5, "max_age_hours": 6, "min_liquidity_usd": 15000,
        "min_holders": 75, "min_social_mentions_1h": 0,
    },
    "exit_rules": {"stop_loss_pct": -25, "max_hold_time_minutes": 240},
    "scoring_weights": {"liquidity": 0.2, "volume_momentum": 0.25, "rugcheck": 0.15},
    "learning": {},
}


def rejected_candidate(symbol="REJ", reason="liquidité 12000 < 15000", price=1.0):
    return Candidate(
        token_address=f"addr_{symbol}", symbol=symbol, name=symbol, chain="solana",
        price_usd=price, liquidity_usd=12000, rejected_reason=reason,
    )


def _fake_verdict(index, family, won, reason=None):
    """Objet minimal accepté par ShadowTracker._append.

    `reason` est distinct de `family` : `missed_rate_by_family` RECALCULE la
    famille depuis le motif, donc un test qui met le nom de la famille dans le
    champ motif ne teste pas ce qu'il croit.
    """
    return type("V", (), {
        "symbol": f"T{index}", "token_address": f"a{family}{index}",
        "reason": reason if reason is not None else family, "reason_family": family,
        "entry_price": 1.0, "peak_price": 3.0 if won else 1.0,
        "last_price": 1.0, "peak_gain_pct": 200.0 if won else 0.0,
        "would_have_won": won, "minutes_tracked": 60.0,
    })()


class TestReasonFamily(unittest.TestCase):
    def test_classe_les_motifs(self):
        self.assertEqual(reason_family("liquidité 12000 < 15000"), "liquidity")
        self.assertEqual(reason_family("holders 40 < 75"), "holders")
        self.assertEqual(reason_family("top wallet 25.0% > 20%"), "concentration")
        self.assertEqual(reason_family("risque critique RugCheck"), "rugcheck")
        self.assertEqual(reason_family("mentions sociales 3 < 15"), "social")


class TestShadowTracker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.tracker = ShadowTracker(os.path.join(self.tmp, "shadow.jsonl"))

    def test_enregistre_les_rejets(self):
        self.assertEqual(self.tracker.record_rejections([rejected_candidate()]), 1)
        self.assertEqual(self.tracker.stats["tracked"], 1)

    def test_ignore_les_rejets_securite(self):
        # On ne relâchera jamais un filtre honeypot, quel que soit le manque à gagner.
        added = self.tracker.record_rejections(
            [rejected_candidate(reason="risque critique RugCheck : ('Honeypot',)")]
        )
        self.assertEqual(added, 0)

    def test_pas_de_doublon(self):
        self.tracker.record_rejections([rejected_candidate()])
        self.tracker.record_rejections([rejected_candidate()])
        self.assertEqual(self.tracker.stats["tracked"], 1)

    def test_suit_le_pic_de_prix(self):
        self.tracker.record_rejections([rejected_candidate(price=1.0)])
        address = self.tracker.tracked_addresses[0]
        self.tracker.update_price(address, 3.0)
        self.tracker.update_price(address, 1.5)  # redescend

        self.tracker._tracked[address]["rejected_at"] = time.time() - 5 * 3600
        verdicts = self.tracker.expire()
        self.assertEqual(len(verdicts), 1)
        self.assertAlmostEqual(verdicts[0].peak_gain_pct, 200.0)
        self.assertTrue(verdicts[0].would_have_won)

    def test_taux_de_manque_par_famille(self):
        for index in range(20):
            self.tracker._append(
                _fake_verdict(index, "liquidity", won=index < 10),
                {"alpha_absolute": 70, "liquidity": 12000, "age_hours": 1},
            )
        stats = self.tracker.missed_rate_by_family(min_sample=10)
        self.assertEqual(stats["liquidity"]["sample"], 20)
        self.assertEqual(stats["liquidity"]["missed_rate"], 50.0)


class TestFamillesAgeEtVolume(unittest.TestCase):
    """L'âge et le volume tombaient dans « autre », que rien ne dessert.

    Mesuré au 2026-08-02 sur les 399 rejets jugés du témoin : 175 rejets
    d'âge et 42 de volume, soit 54 % du total — dont le motif de rejet
    DOMINANT — invisibles à `_relax_from_shadow`.
    """

    def test_trop_vieux_et_trop_jeune_sont_deux_familles(self):
        # Corrections OPPOSÉES : monter le plafond / baisser le plancher.
        # Les confondre relâcherait au hasard.
        self.assertEqual(reason_family("âge 6.0h > 6h"), "age_max")
        self.assertEqual(reason_family("âge 0.1h < 1.5h"), "age_min")

    def test_le_volume_a_sa_famille(self):
        self.assertEqual(reason_family("volume 1h 0$ < 8000$"), "volume")

    def test_ni_lun_ni_lautre_ne_tombe_plus_dans_autre(self):
        for motif in ("âge 14.1h > 6h", "âge 0.4h < 1.5h", "volume 1h 12$ < 8000$"):
            self.assertNotEqual(reason_family(motif), "autre", motif)

    def test_un_motif_inconnu_reste_dans_autre(self):
        self.assertEqual(reason_family("chose jamais vue"), "autre")

    def test_la_famille_est_recalculee_sur_lhistorique(self):
        """Les lignes déjà sur disque portent la taxonomie de leur époque.
        Sans recalcul, élargir la taxonomie ne servirait qu'aux rejets futurs
        — et les 175 rejets d'âge déjà collectés resteraient inutilisables."""
        tmp = tempfile.mkdtemp()
        tracker = ShadowTracker(os.path.join(tmp, "shadow.jsonl"))

        # Écrit avec l'ANCIENNE famille, comme les lignes historiques.
        for index in range(15):
            row = {
                "timestamp": time.time(), "token": f"T{index}",
                "token_address": f"a{index}", "reason": "âge 8.0h > 6h",
                "reason_family": "autre", "entry_price": 1.0, "peak_price": 3.0,
                "last_price": 1.0, "peak_gain_pct": 200.0, "would_have_won": True,
                "minutes_tracked": 60.0,
            }
            with open(tracker.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row) + "\n")

        familles = tracker.missed_rate_by_family(min_sample=15)
        self.assertIn("age_max", familles)
        self.assertNotIn("autre", familles)


class TestBornesEtRelachement(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(self.params_path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.tracker = ShadowTracker(os.path.join(self.tmp, "shadow.jsonl"))
        self.engine = LearningEngine(self.params, self.journal, shadow=self.tracker)

    # Motifs RÉELS, tels que `pipeline._rejection_reason` les écrit. La famille
    # est recalculée depuis le motif : un nom de famille dans ce champ ne
    # testerait rien.
    MOTIFS = {
        "liquidity": "liquidité 12000$ < 15000$",
        "age_max": "âge 8.0h > 6h",
        "age_min": "âge 0.2h < 0.5h",
        "volume": "volume 1h 120$ < 8000$",
    }

    def _shadow_rows(self, count, family, won):
        for index in range(count):
            self.tracker._append(
                _fake_verdict(index, family, won, self.MOTIFS.get(family, family)),
                {"alpha_absolute": 70, "liquidity": 12000, "age_hours": 1},
            )

    def test_borne_empeche_le_cliquet_infini(self):
        # min_age_hours plafonné à 3.0 : au-delà, plus aucun ajustement.
        self.params.set("filters.min_age_hours", 3.0, log=False)
        self.assertIsNone(self.engine._bounded_set("filters.min_age_hours", 3.5, "test", 10))
        self.assertEqual(self.params.get("filters.min_age_hours"), 3.0)

    def test_valeur_clampee_a_la_borne(self):
        change = self.engine._bounded_set("filters.min_liquidity_usd", 500000, "test", 10)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 100000)
        self.assertIn("100000", change)

    def test_relache_un_filtre_qui_rejette_des_gagnants(self):
        self._shadow_rows(15, "liquidity", won=True)
        changes = self.engine._relax_from_shadow()
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 10000)
        self.assertTrue(changes)

    def test_ne_relache_pas_si_les_rejets_etaient_justes(self):
        self._shadow_rows(20, "liquidity", won=False)
        self.assertEqual(self.engine._relax_from_shadow(), [])
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_ne_relache_pas_sous_le_seuil_dechantillon(self):
        self._shadow_rows(5, "liquidity", won=True)
        self.assertEqual(self.engine._relax_from_shadow(), [])
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_relache_le_plafond_dage_dans_le_bon_sens(self):
        self._shadow_rows(15, "age_max", won=True)
        self.engine._relax_from_shadow()
        # Trop vieux -> le plafond MONTE. L'abaisser rejetterait encore plus.
        self.assertEqual(self.params.get("filters.max_age_hours"), 8)

    def test_relache_le_plancher_dage_dans_le_bon_sens(self):
        self._shadow_rows(15, "age_min", won=True)
        self.engine._relax_from_shadow()
        # Trop jeune -> le plancher DESCEND.
        self.assertEqual(self.params.get("filters.min_age_hours"), 0.0)

    def test_relache_le_volume(self):
        self.params.set("filters.min_volume_1h", 8000, log=False)
        self._shadow_rows(15, "volume", won=True)
        self.engine._relax_from_shadow()
        self.assertEqual(self.params.get("filters.min_volume_1h"), 6000)

    def test_relachement_atteignable_avec_zero_trade(self):
        """LA POULE ET L'ŒUF VERROUILLÉE ICI.

        `run()` retournait « (en attente) 0/15 trades » AVANT d'atteindre
        `_relax_from_shadow`. Or un bras dont les filtres coupent tout ne
        trade pas, donc n'atteint jamais 15 trades, donc ne peut jamais
        découvrir que ses filtres coupent trop. Le seul contrepoids au
        resserrage était inaccessible exactement dans la situation qui le
        réclame — même famille de bug que celui corrigé dans `economics`.

        `_relax_from_shadow` ne lit aucun trade : il lit les rejets suivis.
        Rien ne justifiait de le mettre derrière cette porte.
        """
        self._shadow_rows(15, "liquidity", won=True)
        self.assertEqual(len(self.journal.read_positions()), 0)

        changes = self.engine.run()

        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 10000)
        self.assertTrue(any("min_liquidity_usd" in c for c in changes), changes)

    def test_zero_trade_dit_toujours_que_le_reste_attend(self):
        """« rien ajusté » et « pas assez de données » restent distincts."""
        changes = self.engine.run()
        self.assertTrue(any("en attente" in c for c in changes), changes)
        self.assertTrue(any("0/15" in c for c in changes), changes)

    def test_zero_trade_najuste_rien_dautre_que_le_relachement(self):
        """Le garde-fou d'échantillon reste entier : seul le shadow passe."""
        self._shadow_rows(15, "liquidity", won=True)
        avant = dict(self.params.get("exit_rules", {}))

        self.engine.run()

        self.assertEqual(self.params.get("exit_rules", {}), avant)


class TestRelachementSurInactivite(unittest.TestCase):
    """Un bras qui n'entre plus est bloqué par ses propres seuils.

    LE TROU QUE ÇA BOUCHE. `_relax_from_shadow` exige des rejets suivis 4 h,
    jugés, et `SHADOW_MIN_SAMPLE` par famille. Les autres ajustements exigent
    15 trades. Un bras qui n'entre JAMAIS n'a ni les uns ni les autres :
    mesuré au 2026-08-02, `narrative` était à 0 entrée sur 927 cycles.

    Ici la preuve n'est pas « ses rejets auraient gagné » mais « il ne joue
    pas ». Les deux méritent un relâchement, pour des raisons différentes.
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(self.params_path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))

    def _engine(self, cycles, motif, frozen=False):
        return LearningEngine(
            self.params, self.journal,
            inactivity=lambda: (cycles, list(motif) if isinstance(motif, list) else ([motif] if motif else [])),
            frozen=frozen,
        )

    def test_relache_le_seuil_du_motif_dominant(self):
        engine = self._engine(INACTIVITY_CYCLES, "liquidité 12000$ < 15000$")
        self.assertTrue(engine._relax_from_inactivity())
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 10000)

    def test_ne_relache_pas_sous_le_seuil(self):
        engine = self._engine(INACTIVITY_CYCLES - 1, "liquidité 12000$ < 15000$")
        self.assertEqual(engine._relax_from_inactivity(), [])
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_le_temoin_gele_nest_jamais_relache(self):
        """Le témoin est la seule référence comparable aux trades historiques.
        Un relâchement automatique la détruirait, et la comparaison avec."""
        engine = self._engine(INACTIVITY_CYCLES * 3, "liquidité 12000$ < 15000$",
                              frozen=True)
        self.assertEqual(engine._relax_from_inactivity(), [])
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_un_motif_de_securite_nest_pas_relache(self):
        """Aucun manque à gagner ne justifie de desserrer un garde-fou."""
        engine = self._engine(INACTIVITY_CYCLES * 3, "rugcheck 40 < 70")
        self.assertEqual(engine._relax_from_inactivity(), [])
        self.assertEqual(self.params.get("filters.min_rugcheck_score"), None)

    def test_aucun_motif_ne_desserre_rien(self):
        """Un bras qui ne voit RIEN passer n'a pas un seuil trop strict : son
        problème est en amont, dans la fenêtre de découverte."""
        avant = dict(self.params.get("filters", {}))
        engine = self._engine(INACTIVITY_CYCLES * 3, None)
        self.assertEqual(engine._relax_from_inactivity(), [])
        self.assertEqual(self.params.get("filters", {}), avant)

    def test_le_seuil_alpha_est_relachable(self):
        """Un bras dont les candidats passent les filtres et meurent au seuil
        alpha ne sera JAMAIS débloqué en desserrant un filtre. C'était le cas
        de `narrative` (869 cycles) et `consensus` (383) au 2026-08-02."""
        self.params.set("scan.alpha_score_entry_threshold", 70, log=False)
        engine = self._engine(INACTIVITY_CYCLES, "alpha N< N")
        self.assertTrue(engine._relax_from_inactivity())
        self.assertEqual(self.params.get("scan.alpha_score_entry_threshold"), 67.5)

    def test_le_seuil_alpha_ne_descend_pas_sous_sa_borne(self):
        """Sans borne, le relâchement automatique deviendrait un désarmement :
        un bras durablement inactif finirait par acheter n'importe quoi."""
        self.params.set("scan.alpha_score_entry_threshold", 70, log=False)
        engine = self._engine(INACTIVITY_CYCLES, "alpha N< N")
        for _ in range(30):
            engine._relax_from_inactivity()
        self.assertEqual(self.params.get("scan.alpha_score_entry_threshold"), 55)

    def test_un_seul_parametre_par_passage(self):
        """Desserrer cinq seuils d'un coup rend l'effet illisible."""
        engine = self._engine(INACTIVITY_CYCLES, "âge 8.0h > 6h")
        changes = engine._relax_from_inactivity()
        self.assertEqual(len(changes), 1)
        self.assertEqual(self.params.get("filters.max_age_hours"), 8)

    def test_un_relachement_qui_resserrerait_est_refuse(self):
        """LE PIÈGE : les bornes sont GLOBALES, la config d'un bras ne l'est
        pas. `quality` porte `max_age_hours = 48`, borne `(2, 24)`. Relâcher
        de +2 h donne 50, clampé à 24 : la fenêtre passerait de 48 h à 24 h.
        Un relâchement qui divise la fenêtre par deux est un resserrage."""
        self.params.set("filters.max_age_hours", 48.0, log=False)
        engine = self._engine(INACTIVITY_CYCLES, "âge 60.0h > 48.0h")

        self.assertEqual(engine._relax_from_inactivity(), [])
        self.assertEqual(self.params.get("filters.max_age_hours"), 48.0)

    def test_le_meme_refus_protege_le_relachement_par_le_shadow(self):
        self.params.set("filters.max_age_hours", 48.0, log=False)
        engine = LearningEngine(self.params, self.journal)
        self.assertIsNone(engine._relax_set("filters.max_age_hours", 2, "test", 20))
        self.assertEqual(self.params.get("filters.max_age_hours"), 48.0)

    def test_un_relachement_dans_les_bornes_passe_normalement(self):
        self.params.set("filters.max_age_hours", 6.0, log=False)
        engine = LearningEngine(self.params, self.journal)
        self.assertIsNotNone(engine._relax_set("filters.max_age_hours", 2, "test", 20))
        self.assertEqual(self.params.get("filters.max_age_hours"), 8.0)

    def test_descend_la_liste_quand_le_motif_dominant_est_a_sa_borne(self):
        """LE CAS `quality` : bloqué sur l'âge à 24 h, son plafond. S'arrêter
        au motif dominant laisserait le bras inactif indéfiniment sans rien
        tenter, ce qui viderait ce mécanisme de son sens."""
        self.params.set("filters.max_age_hours", 24.0, log=False)
        engine = self._engine(
            INACTIVITY_CYCLES,
            ["âge 30.0h > 24.0h", "liquidité 9000$ < 15000$"],
        )

        changes = engine._relax_from_inactivity()

        self.assertEqual(self.params.get("filters.max_age_hours"), 24.0)
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 10000)
        self.assertEqual(len(changes), 1)

    def test_un_seul_changement_meme_avec_plusieurs_motifs_desserrables(self):
        engine = self._engine(
            INACTIVITY_CYCLES,
            ["liquidité 9000$ < 15000$", "âge 8.0h > 6h"],
        )
        self.assertEqual(len(engine._relax_from_inactivity()), 1)
        self.assertEqual(self.params.get("filters.max_age_hours"), 6)

    def test_tous_les_motifs_bloques_ne_change_rien(self):
        self.params.set("filters.max_age_hours", 24.0, log=False)
        self.params.set("filters.min_liquidity_usd", 5000, log=False)
        engine = self._engine(
            INACTIVITY_CYCLES,
            ["âge 30.0h > 24.0h", "liquidité 1$ < 5000$", "chose inconnue"],
        )
        self.assertEqual(engine._relax_from_inactivity(), [])

    def test_sans_lecteur_dinactivite_rien_ne_bouge(self):
        engine = LearningEngine(self.params, self.journal)
        self.assertEqual(engine._relax_from_inactivity(), [])

    def test_run_relache_meme_a_zero_trade(self):
        """La porte des 15 trades ne doit pas bloquer ce chemin non plus."""
        engine = self._engine(INACTIVITY_CYCLES, "liquidité 12000$ < 15000$")
        changes = engine.run()
        self.assertTrue(any("min_liquidity_usd" in c for c in changes), changes)
        self.assertTrue(any("en attente" in c for c in changes), changes)

    def test_la_borne_arrete_le_relachement_infini(self):
        """`min_liquidity_usd` est borné à 5000 : un bras durablement inactif
        ne peut pas descendre à zéro et acheter n'importe quoi."""
        engine = self._engine(INACTIVITY_CYCLES, "liquidité 1$ < 15000$")
        for _ in range(20):
            engine._relax_from_inactivity()
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 5000)


class TestBacktestValidation(unittest.TestCase):
    """Étape 6.4 : un ajustement de filtres doit prouver son gain."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(PARAMS, fh)
        self.params = ParamsStore(self.params_path)
        self.journal = TradeJournal(os.path.join(self.tmp, "trades.jsonl"))
        self.engine = LearningEngine(self.params, self.journal)

    def _trade(self, pnl_usd, liquidity, age=2.0):
        self.journal._append({
            "is_final_exit": True, "pnl_usd": pnl_usd, "pnl_pct": pnl_usd,
            "liquidity_at_entry": liquidity, "age_hours_at_entry": age,
            "holders_at_entry": 500, "social_score": None, "rugcheck_score": 90,
            "exit_reason": "STOP_LOSS" if pnl_usd < 0 else "TAKE_PROFIT_1",
        })

    def test_simulate_exclut_les_trades_hors_filtres(self):
        for _ in range(10):
            self._trade(-20, liquidity=12000)
        for _ in range(10):
            self._trade(+50, liquidity=60000)

        rows = self.journal.read_final_exits()
        large = self.engine.simulate_filters(rows, {"min_liquidity_usd": 15000})
        self.assertEqual(large["trades"], 10)
        self.assertEqual(large["total_pnl_usd"], 500)
        self.assertEqual(large["win_rate"], 100.0)

    def test_annule_un_changement_sans_amelioration(self):
        for _ in range(20):
            self._trade(+10, liquidity=60000)
        avant = self.params.get("filters")
        self.params.set("filters.min_liquidity_usd", 20000, log=False)

        self.assertIn("annulés", self.engine.validate_filter_changes(avant))
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_valide_un_changement_qui_ecarte_les_perdants(self):
        # Les perdants doivent être AU-DESSUS du filtre d'origine (15000),
        # sinon ils n'auraient jamais été pris et le backtest ne voit rien.
        for _ in range(10):
            self._trade(-30, liquidity=20000)
        for _ in range(10):
            self._trade(+40, liquidity=60000)
        avant = self.params.get("filters")
        self.params.set("filters.min_liquidity_usd", 30000, log=False)

        self.assertIn("validés", self.engine.validate_filter_changes(avant))
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 30000)

    def test_annule_des_filtres_trop_restrictifs(self):
        for _ in range(20):
            self._trade(+10, liquidity=20000)
        avant = self.params.get("filters")
        self.params.set("filters.min_liquidity_usd", 90000, log=False)  # ne garde rien

        self.assertIn("trop restrictifs", self.engine.validate_filter_changes(avant))
        self.assertEqual(self.params.get("filters.min_liquidity_usd"), 15000)

    def test_pas_de_verdict_sous_20_trades(self):
        for _ in range(5):
            self._trade(+10, liquidity=60000)
        self.assertIsNone(self.engine.validate_filter_changes(self.params.get("filters")))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestVerrouModeReel(unittest.TestCase):
    """Le passage en LIVE exige une autorisation explicite du propriétaire."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.params_path = os.path.join(self.tmp, "params.json")
        self.journal_path = os.path.join(self.tmp, "trades.jsonl")
        self.journal = TradeJournal(self.journal_path)
        self._write_params(authorized=False)

    def _write_params(self, authorized):
        data = dict(PARAMS)
        data["live_mode_authorized_by_owner"] = authorized
        with open(self.params_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        self.params = ParamsStore(self.params_path)
        self.engine = LearningEngine(self.params, self.journal)

    def _excellent_track_record(self):
        # 25 trades, 80% de réussite, profit factor très supérieur à 1.5
        for index in range(25):
            self.journal._append({
                "is_final_exit": True,
                "pnl_usd": 100 if index % 5 else -20,
                "pnl_pct": 100 if index % 5 else -20,
                "exit_reason": "TAKE_PROFIT_1",
            })

    def test_refuse_meme_avec_des_statistiques_excellentes(self):
        self._excellent_track_record()
        allowed, why = self.engine.live_mode_allowed()
        self.assertFalse(allowed)
        self.assertIn("verrou propriétaire", why)

    def test_autorise_seulement_si_le_drapeau_est_leve_ET_les_stats_bonnes(self):
        self._excellent_track_record()
        self._write_params(authorized=True)
        allowed, why = self.engine.live_mode_allowed()
        self.assertTrue(allowed, why)

    def test_drapeau_leve_mais_stats_insuffisantes_reste_bloque(self):
        for _ in range(5):
            self.journal._append({"is_final_exit": True, "pnl_usd": -10,
                                  "pnl_pct": -10, "exit_reason": "STOP_LOSS"})
        self._write_params(authorized=True)
        allowed, why = self.engine.live_mode_allowed()
        self.assertFalse(allowed)
        self.assertIn("20 trades", why)

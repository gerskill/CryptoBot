"""Concentration sectorielle de la flotte — le risque que personne ne mesurait.

LE TROU QUE ÇA BOUCHE. Chaque bras a son plafond de positions
(`risk_rules.max_concurrent_positions`, 1 ou 2 selon le manifeste) et sa
taille bornée à 5 % du capital (`MAX_POSITION_PCT_OF_CAPITAL`). Ces deux
gardes regardent UN portefeuille. Aucune ne regarde les sept ensemble.

Conséquence mesurable : les sept bras jugent le MÊME lot enrichi
(`src/pipeline.py`) à chaque cycle. Quand une meta domine le marché, le même
lot est saturé de tokens de cette meta, et les sept bras — qui diffèrent par
leurs seuils, pas par leur univers — piochent dedans en même temps. Sur le
journal de ce dépôt, TOAD, toadtard, TOADGF, MIDASTOAD, FROGOS et Frock ont
tous été pris ; Bark, Pomeranian, splashdog et HAIRINU aussi. Sept
stratégies indépendantes sur le papier, une seule exposition en pratique.

CE QUE CE MODULE MESURE, ET POURQUOI C'EST CE NIVEAU-LÀ. La garde est
CROISÉE (toute la flotte), jamais interne à un bras : à l'intérieur d'un bras
la concentration est déjà bornée par construction — 2 positions maximum à 5 %
chacune plafonnent l'exposition d'un secteur à 10 % de SON capital. Une garde
intra-bras avec un seuil plus haut ne se déclencherait jamais, et avec un
seuil plus bas elle ferait doublon avec `max_concurrent_positions`. Le seul
endroit où l'exposition n'est bornée par rien, c'est l'agrégat des sept.

POURQUOI CELUI-CI DÉCIDE ALORS QUE `wallets.py` ET `dev_history.py` NE FONT
QUE MESURER. Ces deux-là cherchent un EDGE : prétendre qu'un signal prédit le
gain demande de l'avoir prouvé sur un échantillon, sinon c'est du
surapprentissage — leur docstring le dit et c'est juste. Une garde de risque
répond à une autre question : elle ne prétend pas savoir quel token montera,
elle borne ce qui arrive quand toute une meta descend ensemble. Borner une
queue de distribution ne demande pas d'edge prouvé, seulement que la
concentration soit réelle — et elle l'est, elle est comptée ici.

CLASSIFICATION : SYMBOLE ET NOM, RIEN D'AUTRE. Aucune API du dépôt ne rend de
tag « meta » ou « secteur » — vérifié sur DexScreener, Birdeye, RugCheck,
GMGN et Helius. Ce qu'on a est le texte que le lanceur a choisi, et c'est
précisément le véhicule de la meta : un token de la meta chien s'appelle
Bark, Pomeranian ou HAIRINU parce que c'est comme ça qu'il se vend. Coût :
zéro appel réseau, zéro quota.

TROIS PRUDENCES DANS LE CLASSEUR, chacune contre un faux positif précis :

  1. NON CLASSÉ N'EST JAMAIS UN SECTEUR. Regrouper les inconnus créerait le
     plus gros « secteur » du portefeuille et ferait bloquer la garde sur une
     corrélation qui n'existe pas. Un token non classé n'est jamais contraint
     et ne contraint jamais rien.
  2. SOUS-CHAÎNES SEULEMENT À PARTIR DE `MIN_SUBSTRING_LEN`. « ai » cherché en
     sous-chaîne classe CHAIN, RAIN, MAID et SAIL en meta IA. Les clés
     courtes ne matchent qu'en MOT ENTIER ; les longues (`doge`, `pepe`,
     `trump`) peuvent matcher au milieu d'un mot, où elles sont sans
     ambiguïté.
  3. AMBIGUÏTÉ RÉSOLUE PAR UN ORDRE FIXE, PAS PAR LE HASARD DU DICTIONNAIRE.
     « AIDOGE » touche IA et chien. `SECTOR_PRIORITY` tranche toujours pareil.
     Le choix est arbitraire ; ce qui compte pour une garde, c'est qu'il soit
     STABLE — le même token doit tomber dans le même secteur à chaque appel,
     sinon l'exposition mesurée dépend de l'ordre d'itération.

CE MODULE NE FAIT AUCUNE E/S. Il reçoit des positions déjà en mémoire et rend
un verdict. Testable sans réseau ni fichier, comme `positions.py` et
`economics.py`.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Iterable, Optional

# TROIS RÉGIMES DE CORRESPONDANCE SELON LA LONGUEUR DE LA CLÉ. Un seul seuil
# ne marchait pas : « SOLCAT » et « HAIRINU » sont des cas réels du journal
# que le mot entier rate, mais autoriser « cat » n'importe où classe CATALYST
# et LOCATE en meta chat.
#
#   >= 4 lettres   n'importe où dans le texte. « doge » dans AIDOGE, « toad »
#                  dans MIDASTOAD, « trump » dans TRUMPCAT : à cette longueur
#                  la collision fortuite ne s'observe pas sur les 394 symboles
#                  du journal.
#   == 3 lettres   EN FIN DE MOT SEULEMENT. Un ticker de memecoin met son
#                  nom-tête à la fin (SOLCAT, HAIRINU, splashdog), et le
#                  suffixe est le seul régime qui les attrape sans prendre
#                  CATALYST (« cat » au début) ni LOCATE (au milieu).
#   <= 2 lettres   mot entier uniquement. « ai » en sous-chaîne classerait
#                  CHAIN, RAIN, MAID et SAIL en meta IA.
#
# Conséquence assumée : « dogwifhat » reste non classé (« dog » y est en
# préfixe). Le préfixe n'est pas ouvert parce qu'il prend CATALYST et DOGMA —
# et rater un token coûte moins cher qu'inventer une corrélation.
SUBSTRING_ANYWHERE_LEN = 4
SUBSTRING_SUFFIX_LEN = 3

# EXCEPTIONS : clés assez longues pour le régime « n'importe où », mais qui
# vivent aussi à l'intérieur de mots courants. Rétrogradées au mot entier.
# Chacune vient d'un faux positif CONSTATÉ sur les 395 symboles du journal,
# pas d'une crainte théorique :
#     mars   -> « Marshal » classé en meta espace
#     stake  -> « MISTAKE » classé en meta finance
#     apes   -> « GRAPES », « ESCAPES »
#     bear   -> « BEARD »
#     bond   -> « VAGABOND »
WORD_ONLY_KEYS = frozenset({"mars", "stake", "apes", "bear", "bond"})

# Nombre minimal de positions ouvertes dans la flotte avant que le test de
# PART D'EXPOSITION ait un sens. Avec 2 positions ouvertes, un secteur pèse
# trivialement 50 % ou 100 % — refuser une entrée là-dessus bloquerait le bot
# en permanence au démarrage, sur une statistique qui ne mesure rien. Ce n'est
# pas un réglage de politique mais un plancher de validité : constante, pas
# paramètre.
MIN_FLEET_POSITIONS_FOR_SHARE = 4

# Ordre de résolution des ambiguïtés. Du plus spécifique au plus générique :
# un token qui touche « politique » ET « animal » appartient à la meta
# politique (l'animal n'y est qu'un véhicule graphique), jamais l'inverse.
SECTOR_PRIORITY = (
    "politique",
    "celebrite",
    "ia",
    "grenouille",
    "chien",
    "chat",
    "espace",
    "finance",
    "nourriture",
    "animal",
)

# Mots-clés par secteur. Liste volontairement COURTE et conservatrice : rater
# un token (il reste non classé, donc non contraint) coûte moins cher que
# d'inventer une corrélation entre deux tokens sans rapport.
SECTOR_KEYWORDS: dict[str, frozenset[str]] = {
    "chien": frozenset({
        "dog", "doge", "shib", "shiba", "inu", "puppy", "corgi",
        "husky", "poodle", "pomeranian", "retriever", "bark", "woof", "bork",
        "wif", "hound", "beagle", "dachshund", "pitbull", "chihuahua",
        "labrador", "terrier", "lobo", "wolf", "canine",
    }),
    "chat": frozenset({
        "cat", "kitty", "kitten", "meow", "feline", "tabby", "siamese",
        # « lion » a été retiré : il vit dans MILLION, BILLION, STALLION et
        # dans « SeAlⁱon », classé à tort en meta chat sur le journal.
        "purr", "catto", "nyan", "garfield", "tiger",
    }),
    "grenouille": frozenset({
        "pepe", "frog", "froggy", "toad", "kek", "tadpole", "ribbit",
        "amphibian", "frock",
    }),
    "ia": frozenset({
        # « agi » retiré : en suffixe il attrape MAGI et ne vaut pas le risque.
        "ai", "gpt", "llm", "bot", "robot", "neural", "agent",
        "claude", "claudius", "gemini", "openai", "chatgpt", "cyborg",
        "android", "singularity", "sentient",
    }),
    "politique": frozenset({
        "trump", "biden", "maga", "potus", "election", "kamala", "vance",
        "president", "senate", "congress", "gop", "democrat", "republican",
        "politics", "putin", "obama",
    }),
    "celebrite": frozenset({
        "elon", "musk", "tesla", "zuck", "bezos", "kanye",
        "ronaldo", "messi", "oprah", "beyonce", "mrbeast",
    }),
    "espace": frozenset({
        "moon", "mars", "rocket", "space", "astro", "lunar", "galaxy",
        "cosmos", "orbit", "nasa", "starship", "asteroid", "comet",
        "saturn", "nebula",
    }),
    "nourriture": frozenset({
        "pizza", "burger", "taco", "sushi", "banana", "coffee", "beer",
        "bacon", "noodle", "ramen", "donut", "cookie", "waffle", "pancake",
        "sandwich", "chicken", "cheese", "candy",
    }),
    "finance": frozenset({
        "bank", "yield", "stake", "vault", "bond", "hedge",
        "treasury", "dividend", "nasdaq",
    }),
    "animal": frozenset({
        "ape", "apes", "monkey", "gorilla", "sheep", "goat", "bull",
        "bear", "squirrel", "hamster", "penguin", "duck", "goose", "owl",
        "shark", "whale", "dolphin", "eagle", "snake", "turtle",
        "mouse", "bunny", "rabbit", "panda", "koala", "sloth",
        "hippo", "rhino", "giraffe", "elephant", "capybara", "otter",
    }),
}

# Index inverse construit une fois : clé -> secteur, en respectant
# `SECTOR_PRIORITY` quand une même clé apparaîtrait dans deux secteurs.
_KEYWORD_SECTOR: dict[str, str] = {}
for _sector in SECTOR_PRIORITY:
    for _key in SECTOR_KEYWORDS.get(_sector, ()):
        _KEYWORD_SECTOR.setdefault(_key, _sector)

# Clés éligibles à chaque régime, figées une fois : la boucle de `classify`
# tourne sur chaque candidat de chaque cycle, refiltrer le dictionnaire
# complet à chaque appel serait du travail répété pour rien.
_ANYWHERE_KEYS: tuple[tuple[str, str], ...] = tuple(
    (key, sector)
    for key, sector in _KEYWORD_SECTOR.items()
    if len(key) >= SUBSTRING_ANYWHERE_LEN and key not in WORD_ONLY_KEYS
)
_SUFFIX_KEYS: tuple[tuple[str, str], ...] = tuple(
    (key, sector)
    for key, sector in _KEYWORD_SECTOR.items()
    if len(key) == SUBSTRING_SUFFIX_LEN and key not in WORD_ONLY_KEYS
)

_SECTOR_RANK = {name: rank for rank, name in enumerate(SECTOR_PRIORITY)}
_WORD_SPLIT = re.compile(r"[^a-z0-9]+")


def _normalise(text: str) -> str:
    """Minuscules, sans accents ni ponctuation — « BOIÚNA » -> « boiuna »."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    return stripped.lower()


def classify(symbol: str, name: str = "") -> Optional[str]:
    """Secteur (meta) du token, ou `None` si aucun ne ressort clairement.

    `None` n'est PAS un secteur « divers » : c'est une absence de mesure, et
    l'appelant ne doit rien en déduire (cf. l'invariant du dépôt, « donnée
    absente ne rejette jamais »).
    """
    blob = f"{_normalise(symbol)} {_normalise(name)}".strip()
    if not blob:
        return None

    words = [w for w in _WORD_SPLIT.split(blob) if w]
    hits: set[str] = set()
    for word in words:
        sector = _KEYWORD_SECTOR.get(word)
        if sector:
            hits.add(sector)

    # Puis les sous-chaînes, pour les symboles collés (« MIDASTOAD »,
    # « SOLCAT », « toadtard ») que le découpage en mots ne sépare pas.
    compact = _WORD_SPLIT.sub("", blob)
    for key, sector in _ANYWHERE_KEYS:
        if sector not in hits and key in compact:
            hits.add(sector)
    for key, sector in _SUFFIX_KEYS:
        if sector in hits:
            continue
        # Suffixe de N'IMPORTE QUEL mot, et du texte recollé : « SOL CAT »,
        # « SOLCAT » et « Solana Cat » doivent tomber au même endroit.
        if compact.endswith(key) or any(w.endswith(key) for w in words):
            hits.add(sector)

    if not hits:
        return None
    return min(hits, key=lambda s: _SECTOR_RANK[s])


@dataclass(frozen=True)
class SectorExposure:
    """Ce qu'un secteur pèse dans la flotte, en notionnel et en nombre."""

    sector: str
    notional_usd: float
    positions: int
    share_pct: float


@dataclass(frozen=True)
class FleetExposure:
    """Photo de la flotte entière — les sept portefeuilles agrégés."""

    total_notional_usd: float
    total_positions: int
    # Positions dont le secteur est inconnu : comptées dans les totaux (elles
    # occupent bien du capital) mais jamais regroupées en secteur.
    unclassified_positions: int
    by_sector: tuple[SectorExposure, ...]

    def get(self, sector: Optional[str]) -> Optional[SectorExposure]:
        if not sector:
            return None
        for exposure in self.by_sector:
            if exposure.sector == sector:
                return exposure
        return None

    def as_dict(self) -> dict[str, Any]:
        return {
            "total_notional_usd": round(self.total_notional_usd, 2),
            "total_positions": self.total_positions,
            "unclassified_positions": self.unclassified_positions,
            "sectors": {
                e.sector: {
                    "notional_usd": round(e.notional_usd, 2),
                    "positions": e.positions,
                    "share_pct": round(e.share_pct, 1),
                }
                for e in self.by_sector
            },
        }


def position_sector(position: Any) -> Optional[str]:
    """Secteur d'une position ouverte, figé à l'entrée quand il est disponible.

    POURQUOI FIGÉ ET PAS RECALCULÉ. `Position` ne porte que `symbol` ; le
    `name` du token, souvent bien plus parlant (« Pomeranian » contre « PMR »),
    n'existe que sur `Candidate`, au moment de l'entrée. Reclasser plus tard
    depuis le seul symbole donnerait un secteur DIFFÉRENT de celui décidé à
    l'ouverture, et l'exposition mesurée dépendrait du moment où on la regarde.
    Le champ `sector` de `Position` conserve donc la décision d'origine ; ce
    repli sur le symbole ne sert qu'aux positions ouvertes AVANT l'existence
    de ce champ (fichiers `open_positions.json` restaurés).
    """
    stored = getattr(position, "sector", None)
    if stored:
        return stored
    return classify(getattr(position, "symbol", "") or "")


def _notional(position: Any) -> float:
    """Notionnel encore engagé — même convention que `PaperPortfolio.equity`."""
    size = float(getattr(position, "size_usd", 0.0) or 0.0)
    remaining = float(getattr(position, "remaining_fraction", 1.0) or 0.0)
    return max(0.0, size * remaining)


def measure(positions: Iterable[Any]) -> FleetExposure:
    """Agrège les positions ouvertes de TOUS les bras par secteur."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    total_notional = 0.0
    total_positions = 0
    unclassified = 0

    for position in positions:
        notional = _notional(position)
        if notional <= 0:
            continue
        total_notional += notional
        total_positions += 1
        sector = position_sector(position)
        if not sector:
            unclassified += 1
            continue
        totals[sector] = totals.get(sector, 0.0) + notional
        counts[sector] = counts.get(sector, 0) + 1

    by_sector = tuple(
        sorted(
            (
                SectorExposure(
                    sector=sector,
                    notional_usd=notional,
                    positions=counts[sector],
                    share_pct=(100 * notional / total_notional) if total_notional else 0.0,
                )
                for sector, notional in totals.items()
            ),
            key=lambda e: (-e.notional_usd, e.sector),
        )
    )
    return FleetExposure(
        total_notional_usd=total_notional,
        total_positions=total_positions,
        unclassified_positions=unclassified,
        by_sector=by_sector,
    )


def verdict(
    exposure: FleetExposure,
    sector: Optional[str],
    size_usd: float,
    max_sector_positions: int,
    max_sector_exposure_pct: float,
) -> Optional[str]:
    """`None` = entrée autorisée. Sinon, la raison du refus.

    Même contrat que `PaperPortfolio.can_open`, délibérément : la boucle
    traite les deux refus au même endroit et de la même façon.

    DEUX TESTS INDÉPENDANTS, parce qu'ils attrapent deux formes différentes du
    même risque, et qu'aucun ne couvre l'autre :

      NOMBRE     sept petites positions dans la même meta pèsent peu en
                 notionnel mais font quand même sept paris sur un seul
                 événement. `max_sector_positions` les compte.
      PART       deux grosses positions dans la même meta pèsent lourd sans
                 être nombreuses. `max_sector_exposure_pct` les pèse.

    Un seuil à `0` désactive son test — même convention que
    `max_drawdown_stop_pct` et `cooldown_hours` ailleurs dans le dépôt.
    """
    if not sector:
        # Non classé : jamais contraint. Voir le docstring du module.
        return None

    current = exposure.get(sector)
    current_positions = current.positions if current else 0
    current_notional = current.notional_usd if current else 0.0

    if max_sector_positions > 0 and current_positions + 1 > max_sector_positions:
        return (
            f"concentration meta « {sector} » — {current_positions} position(s) "
            f"déjà ouverte(s) dans la flotte, plafond {max_sector_positions}"
        )

    projected_positions = exposure.total_positions + 1
    if max_sector_exposure_pct > 0 and projected_positions >= MIN_FLEET_POSITIONS_FOR_SHARE:
        projected_total = exposure.total_notional_usd + max(0.0, size_usd)
        if projected_total > 0:
            projected_share = 100 * (current_notional + max(0.0, size_usd)) / projected_total
            if projected_share > max_sector_exposure_pct:
                return (
                    f"concentration meta « {sector} » — {projected_share:.0f} % de "
                    f"l'exposition de la flotte après entrée, plafond "
                    f"{max_sector_exposure_pct:.0f} %"
                )
    return None

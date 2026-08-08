#!/usr/bin/env python3
"""Remplacé par `scripts_analyse_sorties.py`.

Ce script ne regardait que les sorties par stop loss, et souffrait de trois
défauts qui faussaient sa mesure :

  - il lisait les lignes `is_final_exit`, donc une position sortie en TP1 puis
    breakeven apparaissait comme une perte de -1,8%, ses +101% invisibles ;
  - son repli sur un seuil de -25 était périmé (le stop loss vaut -10) et
    faussait le « pire cas » ;
  - il libellait « déclenchement médian » ce qui était le dépassement.

Le nouveau rapport garde la mesure du glissement (section 2) et y ajoute les
gagnants, l'atteignabilité des seuils et le comparatif entre stratégies.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if __name__ == "__main__":
    print(
        "Ce script est remplacé par scripts_analyse_sorties.py.\n"
        "  python3 scripts_analyse_sorties.py              # rapport complet\n"
        "  python3 scripts_analyse_sorties.py --sections 2 # glissement du stop loss seul\n"
    )
    from scripts.analyse_sorties import main

    main()

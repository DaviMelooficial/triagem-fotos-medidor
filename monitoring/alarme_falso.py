"""Mede a taxa de alarme falso dos testes de drift quando NÃO há drift (cenário nulo por reamostragem).

Sorteia, com reposição, uma "referência" de 200 e um "lote" de N amostras do MESMO conjunto
(monitoring/referencia.json) e roda drift.comparar. Como as duas vêm da mesma distribuição, todo
alarme é falso. Conta, por rodada, se houve pelo menos um alarme em cada critério.
É uma aproximação: os dois lados saem do mesmo conjunto finito de 200 (4 fotos x 50 variações).

Uso: uv run python monitoring/alarme_falso.py [--rodadas 300] [--lote 170]
"""

import argparse
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import drift  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rodadas", type=int, default=300)
    parser.add_argument("--lote", type=int, default=170)
    args = parser.parse_args()
    ref = drift.carregar_referencia()
    n = ref["n"]
    rng = random.Random(0)
    contagem = {"pelo menos 1 teste com p < 0,05": 0, "pelo menos 1 teste após Bonferroni": 0, "pelo menos 1 PSI com drift": 0}
    por_variavel_psi = {}
    for _ in range(args.rodadas):
        i_ref = [rng.randrange(n) for _ in range(n)]
        i_lote = [rng.randrange(n) for _ in range(args.lote)]
        sub_ref = {"n": n, "hist_l_soma": ref["hist_l_soma"],
                   "variaveis": {k: [v[i] for i in i_ref] for k, v in ref["variaveis"].items()}}
        lote = [{k: v[i] for k, v in ref["variaveis"].items()} for i in i_lote]
        r = drift.comparar(lote, sub_ref)
        contagem["pelo menos 1 teste com p < 0,05"] += bool(r["variaveis_com_drift"])
        contagem["pelo menos 1 teste após Bonferroni"] += bool(r["variaveis_com_drift_bonferroni"])
        contagem["pelo menos 1 PSI com drift"] += bool(r["variaveis_com_drift_psi"])
        for nome in r["variaveis_com_drift_psi"]:
            por_variavel_psi[nome] = por_variavel_psi.get(nome, 0) + 1
    print(f"Cenário sem drift: {args.rodadas} rodadas, referência {n} x lote {args.lote}, {drift.N_TESTES - 1} variáveis")
    for criterio, vezes in contagem.items():
        print(f"  {criterio}: {vezes}/{args.rodadas} = {100 * vezes / args.rodadas:.1f}% das rodadas")
    if por_variavel_psi:
        print("  PSI com alarme falso, por variável:", dict(sorted(por_variavel_psi.items(), key=lambda x: -x[1])))


if __name__ == "__main__":
    main()

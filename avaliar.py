"""Mede o acerto do extrator num lote real (que NÃO vai para o repositório).

Uso: uv run python avaliar.py --pasta <pasta das fotos> --csv <csv do lote> --n 50 --modelo qwen2.5vl:3b
Só imprime números agregados; o detalhe por foto fica em resultados_locais/ (ignorado pelo git).
"""

import argparse
import csv
import random
from pathlib import Path
from statistics import mean

import extrator


def normalizar_numero(texto: str | None) -> str:
    """Número do medidor sem espaços, em maiúsculas e sem zeros à esquerda."""
    return (texto or "").replace(" ", "").upper().lstrip("0")


def acertou_numero(previsto: str | None, esperado: str) -> bool:
    # Algumas linhas trazem dois medidores ("A/B"); vale acertar qualquer um deles.
    return normalizar_numero(previsto) in {normalizar_numero(n) for n in esperado.split("/")}


def acertou_leitura(previsto: str | None, esperado: str) -> bool:
    # Compara como inteiro: "012345" e "12345" são a mesma leitura.
    return bool(previsto) and previsto.isdigit() and int(previsto) == int(esperado)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pasta", required=True, type=Path)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--n", type=int, default=50)
    parser.add_argument("--modelo", default=extrator.MODELO)
    args = parser.parse_args()

    with open(args.csv, encoding="utf-8", newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo, delimiter=";"))
    # Só fotos que existem e que o leiturista conseguiu ler (sem nota de ocorrência).
    validas = [l for l in linhas if l["Nota de Leitura Atual"] == "NA"
               and l["Foto do medidor"] != "NA" and (args.pasta / l["Foto do medidor"]).exists()]
    if not validas:
        raise SystemExit("Nenhuma foto para avaliar: confira --pasta e se o CSV tem linhas com nota NA e foto existente.")
    amostra = random.Random(42).sample(validas, min(args.n, len(validas)))  # seed fixa: amostra repetível

    resultados = []
    for i, linha in enumerate(amostra, 1):
        r = extrator.extrair(args.pasta / linha["Foto do medidor"], modelo=args.modelo)
        resultados.append({
            "foto": linha["Foto do medidor"],
            "numero_esperado": linha["Numero do medidor"], "numero_previsto": r["numero_medidor"],
            "leitura_esperada": linha["Posicao do medidor lida"], "leitura_prevista": r["leitura"],
            "acerto_numero": acertou_numero(r["numero_medidor"], linha["Numero do medidor"]),
            "acerto_leitura": acertou_leitura(r["leitura"], linha["Posicao do medidor lida"]),
            "confianca": r["confianca"]["geral"], "precisa_revisao": r["precisa_revisao"],
            "tempo_segundos": r["tempo_segundos"],
        })
        print(f"{i}/{len(amostra)} processadas", end="\r")

    # Salva ao lado do script (pasta ignorada pelo git), de onde quer que ele seja chamado.
    saida = Path(__file__).parent / "resultados_locais" / f"avaliacao_{args.modelo.replace(':', '_')}.csv"
    saida.parent.mkdir(exist_ok=True)
    with open(saida, "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=resultados[0].keys(), delimiter=";")
        escritor.writeheader()
        escritor.writerows(resultados)

    def pct(campo):
        return 100 * mean(r[campo] for r in resultados)

    # "Acertou tudo" = número e leitura certos; é o que importa para dispensar a revisão.
    tudo = [r["acerto_numero"] and r["acerto_leitura"] for r in resultados]
    conf_acerto = [r["confianca"] for r, ok in zip(resultados, tudo) if ok]
    conf_erro = [r["confianca"] for r, ok in zip(resultados, tudo) if not ok]
    print(f"\nModelo: {args.modelo} | fotos avaliadas: {len(resultados)}")
    print(f"Acerto do número do medidor: {pct('acerto_numero'):.0f}%")
    print(f"Acerto da leitura:           {pct('acerto_leitura'):.0f}%")
    print(f"Acerto dos dois campos:      {100 * mean(tudo):.0f}%")
    print(f"Marcadas para revisão:       {pct('precisa_revisao'):.0f}%")
    print(f"Confiança média quando acerta: {mean(conf_acerto) if conf_acerto else float('nan'):.2f}")
    print(f"Confiança média quando erra:   {mean(conf_erro) if conf_erro else float('nan'):.2f}")
    # Pergunta de negócio: das fotos que o sistema liberaria sem revisão, quantas estavam certas?
    liberadas = [ok for r, ok in zip(resultados, tudo) if not r["precisa_revisao"]]
    if liberadas:
        print(f"Liberadas sem revisão: {len(liberadas)}, com acerto total em {100 * mean(liberadas):.0f}%")
    print(f"Tempo médio por foto: {mean(r['tempo_segundos'] for r in resultados):.1f} s")
    print(f"Detalhe por foto (local, não versionado): {saida}")


if __name__ == "__main__":
    main()

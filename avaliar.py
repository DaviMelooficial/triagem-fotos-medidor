"""Mede o acerto do extrator num lote de fotos com gabarito (o lote de campo, que NÃO vai para o repositório,
ou as fotos da equipe em exemplos/). Campo com "NA" no gabarito fica fora da conta.

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


def acertou_numero(previsto: str | None, esperado: str) -> bool | None:
    if esperado == "NA":
        return None  # sem gabarito para este campo: não entra na conta
    # Algumas linhas trazem dois medidores ("A/B"); vale acertar qualquer um deles.
    return normalizar_numero(previsto) in {normalizar_numero(n) for n in esperado.split("/")}


def acertou_leitura(previsto: str | None, esperado: str) -> bool | None:
    if esperado == "NA":
        return None  # sem gabarito para este campo: não entra na conta
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
    # O nome leva a pasta avaliada e o modelo, para uma avaliação não sobrescrever a outra.
    saida = Path(__file__).parent / "resultados_locais" / f"avaliacao_{args.pasta.resolve().name}_{args.modelo.replace(':', '_')}.csv"
    saida.parent.mkdir(exist_ok=True)
    with open(saida, "w", encoding="utf-8", newline="") as arquivo:
        escritor = csv.DictWriter(arquivo, fieldnames=resultados[0].keys(), delimiter=";")
        escritor.writeheader()
        escritor.writerows(resultados)

    def pct(valores):
        # Ignora os None (campo sem gabarito) e mostra a % e a contagem, ex. "25% (10/40)".
        valores = [v for v in valores if v is not None]
        if not valores:
            return "sem gabarito"
        return f"{100 * mean(valores):.0f}% ({sum(valores)}/{len(valores)})"

    # "Acertou tudo" = número e leitura certos; só conta fotos com gabarito dos dois campos.
    com_gabarito = [r for r in resultados if r["acerto_numero"] is not None and r["acerto_leitura"] is not None]
    tudo = [r["acerto_numero"] and r["acerto_leitura"] for r in com_gabarito]
    conf_acerto = [r["confianca"] for r, ok in zip(com_gabarito, tudo) if ok]
    conf_erro = [r["confianca"] for r, ok in zip(com_gabarito, tudo) if not ok]
    print(f"\nModelo: {args.modelo} | fotos avaliadas: {len(resultados)}")
    print(f"Acerto do número do medidor: {pct(r['acerto_numero'] for r in resultados)}")
    print(f"Acerto da leitura:           {pct(r['acerto_leitura'] for r in resultados)}")
    print(f"Acerto dos dois campos:      {pct(tudo)}")
    print(f"Marcadas para revisão:       {pct(r['precisa_revisao'] for r in resultados)}")
    # Sem nenhum caso (ex.: nenhum erro), imprime "sem casos" em vez de uma média vazia.
    print(f"Confiança média quando acerta: {f'{mean(conf_acerto):.2f}' if conf_acerto else 'sem casos'}")
    print(f"Confiança média quando erra:   {f'{mean(conf_erro):.2f}' if conf_erro else 'sem casos'}")
    # Pergunta de negócio: das fotos que o sistema liberaria sem revisão, quantas estavam certas?
    liberadas = [ok for r, ok in zip(com_gabarito, tudo) if not r["precisa_revisao"]]
    if liberadas:
        print(f"Liberadas sem revisão: {len(liberadas)}, com acerto total em {100 * mean(liberadas):.0f}%")
    print(f"Tempo médio por foto: {mean(r['tempo_segundos'] for r in resultados):.1f} s")
    print(f"Detalhe por foto (local, não versionado): {saida}")


if __name__ == "__main__":
    main()

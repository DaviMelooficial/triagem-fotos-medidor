"""Monta monitoring/analise_metrics.xlsx a partir de fotos (snapshots) reais do /metrics, com FÓRMULAS.

O /metrics só traz contadores acumulados desde que o serviço subiu. Para olhar uma janela de tempo,
tira-se uma foto ANTES e outra DEPOIS e subtrai-se (é o que o rate()/increase() do Prometheus faz).
A planilha faz essa conta e, sobre a janela:
  - aba latencia: buckets do histograma do /extrair -> contagem por faixa (diferença de cumulativos)
    -> histograma; quantis por interpolação linear dentro do bucket (igual ao histogram_quantile do
    Prometheus) -> mínimo/Q1/mediana/Q3/p95/máximo -> boxplot;
  - aba erros: bentoml_service_request_total por endpoint e código -> taxa de erro = códigos >= 400 / total;
  - aba promql: as mesmas contas em PromQL.

Uso:
  uv run python monitoring/planilha_metrics.py --foto antes.txt         (salva o /metrics agora)
  ... gere carga ...
  uv run python monitoring/planilha_metrics.py --foto depois.txt
  uv run python monitoring/planilha_metrics.py --antes antes.txt --depois depois.txt
  Só --depois (sem --antes) = janela desde a subida do serviço.
  Com just: just metrics-foto antes.txt; just carga 50; just metrics-foto depois.txt; just planilha antes.txt depois.txt
"""

import argparse
import math
from collections import defaultdict
from pathlib import Path

import httpx
from openpyxl import Workbook
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from prometheus_client.parser import text_string_to_metric_families

PASTA = Path(__file__).resolve().parent
ENDPOINT = "/extrair"
LATENCIA = "bentoml_service_request_duration_seconds"
TOTAL = "bentoml_service_request_total"
NEGRITO = Font(bold=True)
CABECALHO = PatternFill("solid", fgColor="DCE8F7")
FORMULA = PatternFill("solid", fgColor="FFF4D6")  # células amarelas = fórmula


def ler(caminho: Path | None) -> dict:
    """Devolve {"buckets": {le: cumulativo}, "soma": x, "contagem": n, "totais": {(endpoint, codigo): n}}."""
    dados = {"buckets": defaultdict(float), "soma": 0.0, "contagem": 0.0, "totais": defaultdict(float)}
    if caminho is None:
        return dados
    for familia in text_string_to_metric_families(caminho.read_text(encoding="utf-8")):
        for amostra in familia.samples:
            rotulos = amostra.labels
            if familia.name == LATENCIA and rotulos.get("endpoint") == ENDPOINT:
                # soma os códigos HTTP: a latência é do endpoint inteiro (200, 400 e 500 juntos)
                if amostra.name.endswith("_bucket"):
                    dados["buckets"][float(rotulos["le"])] += amostra.value
                elif amostra.name.endswith("_sum"):
                    dados["soma"] += amostra.value
                elif amostra.name.endswith("_count"):
                    dados["contagem"] += amostra.value
            elif familia.name == TOTAL.removesuffix("_total") and amostra.name == TOTAL:
                dados["totais"][(rotulos["endpoint"], int(rotulos["http_response_code"]))] += amostra.value
    return dados


def _titulo(aba, linha: int, texto: str) -> None:
    aba.cell(linha, 1, texto).font = Font(bold=True, size=12)


def _cabecalho(aba, linha: int, nomes: list[str]) -> None:
    for coluna, nome in enumerate(nomes, start=1):
        celula = aba.cell(linha, coluna, nome)
        celula.font, celula.fill = NEGRITO, CABECALHO
        celula.alignment = Alignment(wrap_text=True, vertical="top")


def aba_latencia(livro, antes: dict, depois: dict) -> None:
    aba = livro.create_sheet("latencia")
    _titulo(aba, 1, f"Latência do {ENDPOINT}: histograma e boxplot a partir dos buckets do /metrics (janela = depois - antes)")
    aba["A2"] = "Amarelo = fórmula. Buckets são CUMULATIVOS: o bucket le=5 conta todos os pedidos com duração <= 5 s."
    _cabecalho(aba, 4, ["le (limite superior, s)", "cumulativo ANTES", "cumulativo DEPOIS", "cumulativo na janela",
                        "limite inferior (s)", "pedidos nesta faixa", "fração", "cumulativo anterior", "rótulo da faixa"])
    limites = sorted(depois["buckets"])
    primeira = 5
    for i, le in enumerate(limites):
        linha = primeira + i
        aba.cell(linha, 1, "+Inf" if math.isinf(le) else le)
        aba.cell(linha, 2, antes["buckets"].get(le, 0))
        aba.cell(linha, 3, depois["buckets"][le])
        aba.cell(linha, 4, f"=C{linha}-B{linha}")
        aba.cell(linha, 5, 0 if i == 0 else f"=A{linha - 1}")
        aba.cell(linha, 6, f"=D{linha}" if i == 0 else f"=D{linha}-D{linha - 1}")  # diferença de cumulativos
        aba.cell(linha, 8, 0 if i == 0 else f"=D{linha - 1}")
        aba.cell(linha, 9, f'=E{linha}&"-"&A{linha}&" s"')
    ultima = primeira + len(limites) - 1
    for linha in range(primeira, ultima + 1):
        aba.cell(linha, 7, f"=IF($D${ultima}=0,0,F{linha}/$D${ultima})").number_format = "0.0%"
        for coluna in (4, 5, 6, 7, 8, 9):
            aba.cell(linha, coluna).fill = FORMULA

    # Resumo e quantis -------------------------------------------------------------
    base = ultima + 2
    faixa = lambda c: f"${c}${primeira}:${c}${ultima}"  # noqa: E731
    aba.cell(base, 1, "Resumo da janela").font = NEGRITO
    aba.cell(base + 1, 1, "pedidos (count)")
    aba.cell(base + 1, 2, f"=D{ultima}")
    aba.cell(base + 2, 1, "soma das durações (s)")
    aba.cell(base + 2, 2, depois["soma"] - antes["soma"])
    aba.cell(base + 3, 1, "média (s) = soma / count")
    aba.cell(base + 3, 2, f"=IF(B{base + 1}=0,0,B{base + 2}/B{base + 1})")
    linha_q = base + 5
    _cabecalho(aba, linha_q, ["quantil", "q", "posição = q x count", "índice do bucket", "limite inferior",
                              "limite superior", "cumulativo anterior", "pedidos no bucket", "valor estimado (s)"])
    quantis = [("mínimo (aprox.)", 0.0), ("Q1", 0.25), ("mediana", 0.5), ("Q3", 0.75), ("p95", 0.95), ("máximo (aprox.)", 1.0)]
    for j, (nome, q) in enumerate(quantis):
        linha = linha_q + 1 + j
        aba.cell(linha, 1, nome)
        aba.cell(linha, 2, q)
        aba.cell(linha, 3, f"=B{linha}*$B${base + 1}")
        # índice do primeiro bucket cujo cumulativo >= posição (os cumulativos só crescem, então basta
        # contar quantos ficam abaixo); para o mínimo, o primeiro bucket com algum pedido.
        if q == 0:
            aba.cell(linha, 4, f"=MIN(COUNTIF({faixa('D')},0)+1,{len(limites)})")
        else:
            aba.cell(linha, 4, f'=MIN(COUNTIF({faixa("D")},"<"&C{linha})+1,{len(limites)})')
        aba.cell(linha, 5, f"=INDEX({faixa('E')},D{linha})")
        aba.cell(linha, 6, f"=INDEX({faixa('A')},D{linha})")
        aba.cell(linha, 7, f"=INDEX({faixa('H')},D{linha})")
        aba.cell(linha, 8, f"=INDEX({faixa('F')},D{linha})")
        if q == 0:
            valor = f"=E{linha}"  # só se sabe que o menor pedido está nesse bucket: usamos o limite inferior
        else:
            # interpolação linear dentro do bucket; no bucket +Inf não há limite superior: fica o inferior
            valor = f"=IF(ISNUMBER(F{linha}),IF(H{linha}=0,E{linha},E{linha}+(F{linha}-E{linha})*(C{linha}-G{linha})/H{linha}),E{linha})"
        aba.cell(linha, 9, valor).number_format = "0.000"
        for coluna in range(3, 10):
            aba.cell(linha, coluna).fill = FORMULA
    nota = linha_q + len(quantis) + 2
    aba.cell(nota, 1, "Como ler: o valor é uma ESTIMATIVA. Dentro de um bucket o Prometheus supõe os pedidos espalhados por igual; "
                      "o erro máximo é a largura do bucket. O máximo real fica no log (latencia_total_s).")

    # Boxplot com barras empilhadas: base invisível = mínimo; depois Q1-min, med-Q1, Q3-med, máx-Q3.
    caixa = nota + 2
    aba.cell(caixa, 1, "Dados do boxplot (barras empilhadas)").font = NEGRITO
    _cabecalho(aba, caixa + 1, ["série", "base (mín)", "haste inferior (Q1-mín)", "caixa Q1-mediana", "caixa mediana-Q3", "haste superior (máx-Q3)"])
    linha_box = caixa + 2
    q = {nome: f"$I${linha_q + 1 + j}" for j, (nome, _) in enumerate(quantis)}
    aba.cell(linha_box, 1, ENDPOINT)
    aba.cell(linha_box, 2, f"={q['mínimo (aprox.)']}")
    aba.cell(linha_box, 3, f"={q['Q1']}-{q['mínimo (aprox.)']}")
    aba.cell(linha_box, 4, f"={q['mediana']}-{q['Q1']}")
    aba.cell(linha_box, 5, f"={q['Q3']}-{q['mediana']}")
    aba.cell(linha_box, 6, f"={q['máximo (aprox.)']}-{q['Q3']}")
    for coluna in range(2, 7):
        aba.cell(linha_box, coluna).fill = FORMULA

    histograma = BarChart()
    histograma.title = f"Histograma da latência do {ENDPOINT} (pedidos por faixa)"
    histograma.y_axis.title, histograma.x_axis.title = "pedidos", "faixa de duração"
    histograma.add_data(Reference(aba, min_col=6, min_row=4, max_row=ultima), titles_from_data=True)
    histograma.set_categories(Reference(aba, min_col=9, min_row=primeira, max_row=ultima))
    histograma.gapWidth, histograma.legend, histograma.width, histograma.height = 10, None, 22, 9
    aba.add_chart(histograma, "K4")

    boxplot = BarChart()
    boxplot.type, boxplot.grouping, boxplot.overlap = "bar", "stacked", 100
    boxplot.title = f"Boxplot aproximado da latência do {ENDPOINT} (s)"
    boxplot.add_data(Reference(aba, min_col=2, max_col=6, min_row=caixa + 1, max_row=linha_box), titles_from_data=True)
    boxplot.set_categories(Reference(aba, min_col=1, min_row=linha_box))
    cores = [None, "BFBFBF", "2A78D6", "2A78D6", "BFBFBF"]
    for serie, cor in zip(boxplot.series, cores):
        if cor is None:
            serie.graphicalProperties.noFill = True  # base invisível
            serie.graphicalProperties.line.noFill = True
        else:
            serie.graphicalProperties.solidFill = cor
            serie.graphicalProperties.line.solidFill = "FFFFFF"
    boxplot.width, boxplot.height = 22, 6
    aba.add_chart(boxplot, "K24")
    for coluna in range(1, 10):
        aba.column_dimensions[get_column_letter(coluna)].width = 16
    aba.column_dimensions["A"].width = 22


def aba_erros(livro, antes: dict, depois: dict) -> None:
    aba = livro.create_sheet("erros")
    _titulo(aba, 1, "Taxa de erro a partir de bentoml_service_request_total (janela = depois - antes)")
    _cabecalho(aba, 3, ["endpoint", "código HTTP", "total ANTES", "total DEPOIS", "pedidos na janela"])
    chaves = sorted(depois["totais"])
    for i, (endpoint, codigo) in enumerate(chaves):
        linha = 4 + i
        aba.cell(linha, 1, endpoint)
        aba.cell(linha, 2, codigo)
        aba.cell(linha, 3, antes["totais"].get((endpoint, codigo), 0))
        aba.cell(linha, 4, depois["totais"][(endpoint, codigo)])
        aba.cell(linha, 5, f"=D{linha}-C{linha}").fill = FORMULA
    ultima = 3 + len(chaves)
    r = ultima + 2
    a, b, e = f"$A$4:$A${ultima}", f"$B$4:$B${ultima}", f"$E$4:$E${ultima}"
    aba.cell(r, 1, f"{ENDPOINT}: pedidos na janela")
    aba.cell(r, 5, f'=SUMIFS({e},{a},"{ENDPOINT}")')
    aba.cell(r + 1, 1, f"{ENDPOINT}: erros do cliente (4xx)")
    aba.cell(r + 1, 5, f'=SUMIFS({e},{a},"{ENDPOINT}",{b},">=400",{b},"<500")')
    aba.cell(r + 2, 1, f"{ENDPOINT}: erros do servidor (5xx)")
    aba.cell(r + 2, 5, f'=SUMIFS({e},{a},"{ENDPOINT}",{b},">=500")')
    aba.cell(r + 3, 1, f"{ENDPOINT}: taxa de erro = (4xx + 5xx) / total")
    aba.cell(r + 3, 5, f"=IF(E{r}=0,0,(E{r + 1}+E{r + 2})/E{r})").number_format = "0.00%"
    aba.cell(r + 4, 1, f"{ENDPOINT}: taxa de erro do servidor = 5xx / total")
    aba.cell(r + 4, 5, f"=IF(E{r}=0,0,E{r + 2}/E{r})").number_format = "0.00%"
    for linha in range(r, r + 5):
        aba.cell(linha, 5).fill = FORMULA
        aba.cell(linha, 1).font = NEGRITO
    aba.cell(r + 6, 1, "4xx = culpa de quem enviou (arquivo que não é foto); 5xx = falha nossa (modelo, timeout). Vale olhar as duas separadas.")
    aba.column_dimensions["A"].width = 46
    for coluna in "BCDE":
        aba.column_dimensions[coluna].width = 16


def aba_promql(livro) -> None:
    aba = livro.create_sheet("promql")
    _titulo(aba, 1, "As mesmas contas no Prometheus (janela deslizante, sem precisar de duas fotos)")
    linhas = [
        ("pedidos por faixa na janela (histograma)",
         'sum by (le) (increase(bentoml_service_request_duration_seconds_bucket{endpoint="/extrair"}[5m]))'),
        ("mediana / Q1 / Q3 / p95 da latência",
         'histogram_quantile(0.5, sum by (le) (rate(bentoml_service_request_duration_seconds_bucket{endpoint="/extrair"}[5m])))'),
        ("latência média", 'sum(rate(bentoml_service_request_duration_seconds_sum{endpoint="/extrair"}[5m])) / '
                           'sum(rate(bentoml_service_request_duration_seconds_count{endpoint="/extrair"}[5m]))'),
        ("taxa de erro", 'sum(rate(bentoml_service_request_total{endpoint="/extrair",http_response_code=~"[45].."}[5m])) / '
                         'sum(rate(bentoml_service_request_total{endpoint="/extrair"}[5m]))'),
        ("pedidos por segundo", 'sum(rate(bentoml_service_request_total{endpoint="/extrair"}[5m]))'),
        ("fração que vai para revisão", 'sum(rate(triagem_predicoes_total{status="ok",precisa_revisao="true"}[5m])) / '
                                        'sum(rate(triagem_predicoes_total{status="ok"}[5m]))'),
        ("já gravadas pelas regras", "triagem:taxa_erro:5m, triagem:latencia_p50:5m, triagem:latencia_p95:5m, triagem:fracao_revisao:5m"),
    ]
    _cabecalho(aba, 3, ["o quê", "PromQL"])
    for i, (oque, consulta) in enumerate(linhas):
        aba.cell(4 + i, 1, oque)
        aba.cell(4 + i, 2, consulta)
    aba.cell(5 + len(linhas), 1, "rate(x[5m]) = aumento do contador nos últimos 5 min / 300 s (trata o zeramento quando o serviço reinicia). "
                                  "Q1 e Q3: troque 0.5 por 0.25 e 0.75.")
    aba.column_dimensions["A"].width = 40
    aba.column_dimensions["B"].width = 140


def aba_leiame(livro, antes: Path | None, depois: Path) -> None:
    aba = livro.active
    aba.title = "leia-me"
    textos = [
        "Planilha gerada por monitoring/planilha_metrics.py a partir de fotos reais do GET /metrics.",
        f"Foto ANTES: {antes.name if antes else '(nenhuma: janela = desde a subida do serviço)'}   Foto DEPOIS: {depois.name}",
        "",
        "Por que duas fotos: o /metrics só guarda contadores ACUMULADOS desde que o processo subiu; não há janela de tempo.",
        "A diferença DEPOIS - ANTES dá o que aconteceu entre as duas fotos (o mesmo que increase() no Prometheus).",
        "",
        "Abas: latencia (histograma, quantis e boxplot), erros (taxa de erro), promql (as mesmas contas no Prometheus).",
        "Células amarelas são fórmulas: troque os números das colunas ANTES/DEPOIS por outra foto e tudo se recalcula.",
    ]
    for i, texto in enumerate(textos, start=1):
        aba.cell(i, 1, texto)
    aba.column_dimensions["A"].width = 130


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:3000")
    parser.add_argument("--foto", type=Path, help="só salva o /metrics atual neste arquivo")
    parser.add_argument("--antes", type=Path)
    parser.add_argument("--depois", type=Path)
    parser.add_argument("--saida", type=Path, default=PASTA / "analise_metrics.xlsx")
    args = parser.parse_args()
    if args.foto:
        args.foto.write_text(httpx.get(f"{args.url}/metrics", timeout=10).text, encoding="utf-8")
        print(f"/metrics salvo em {args.foto}")
        return
    if not args.depois:
        parser.error("informe --depois (e, se quiser uma janela, --antes)")
    antes, depois = ler(args.antes), ler(args.depois)
    if not depois["buckets"]:
        raise SystemExit(f"{args.depois} não tem {LATENCIA} do {ENDPOINT}: houve pedido ao serviço antes da foto?")
    livro = Workbook()
    aba_leiame(livro, args.antes, args.depois)
    aba_latencia(livro, antes, depois)
    aba_erros(livro, antes, depois)
    aba_promql(livro)
    livro.save(args.saida)
    print(f"planilha salva em {args.saida}")


if __name__ == "__main__":
    main()

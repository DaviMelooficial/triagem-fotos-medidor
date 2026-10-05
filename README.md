# Triagem de fotos de medidor de energia

Serviço que recebe a **foto de um medidor de energia elétrica** e devolve, em JSON, o **número do medidor**,
a **função** (código do registro no display ou a unidade, em medidores de rolete) e a **leitura**, junto com
uma **confiança** por campo e a indicação se a foto **precisa de revisão humana**.

A ideia é a triagem: as fotos que o modelo lê com segurança seguem direto; as duvidosas vão para uma pessoa.

```
curl (foto)  →  BentoML  POST /extrair  →  Ollama local (modelo de visão Qwen3-VL)  →  JSON
```

Tudo roda localmente: nenhuma foto sai da máquina.

## Equipe

- Davi Melo — dam3@cesar.school
- Elizabete Albuquerque — eba@cesar.school
- Ivan Edward — iers@cesar.school

## Requisitos

| item | versão | observação |
|---|---|---|
| Python | 3.12 | o `uv` baixa sozinho se não houver |
| [uv](https://docs.astral.sh/uv/) | 0.5 ou mais novo | gerenciador de pacotes; instala as versões travadas no `uv.lock` |
| [Ollama](https://ollama.com/download) | 0.32 ou mais novo | servidor local do modelo de visão |
| [just](https://github.com/casey/just) | qualquer | **opcional**; só encurta os comandos |
| disco | ~4 GB | ~3,3 GB do modelo `qwen3-vl:4b` + ~0,5 GB do ambiente Python |
| memória | 8 GB de RAM livre | o modelo ocupa ~4–5 GB quando carregado |

Tempo aproximado de cada passo (medido num Mac Apple Silicon com 24 GB; numa máquina sem GPU tende a ser bem mais lento):

| passo | tempo |
|---|---|
| `uv sync` (instalar dependências) | ~1 min |
| `ollama pull qwen3-vl:4b` (baixar o modelo) | 3–10 min, depende da internet |
| subir o serviço | ~5 s |
| primeira predição (carrega o modelo na memória) | ~15–30 s |
| predições seguintes | ~4–15 s por foto |
| `just testar` | ~1 min |

## Do clone à primeira predição

### 1. Instalar as ferramentas (uma vez por máquina)

Mac / Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh     # instala o uv
# Ollama: baixe e instale em https://ollama.com/download (no Linux: curl -fsSL https://ollama.com/install.sh | sh)
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# Ollama: baixe e instale em https://ollama.com/download
```

Depois de instalar, **abra o app do Ollama** (ou rode `ollama serve` num terminal) e confira com `ollama --version`.

### 2. Clonar, instalar e baixar o modelo

Com `just`:

```bash
git clone https://github.com/DaviMelooficial/triagem-fotos-medidor.git
cd triagem-fotos-medidor
just setup        # uv sync
just modelo       # ollama pull qwen3-vl:4b
```

Sem `just` (funciona igual no Mac, Linux e Windows):

```bash
git clone https://github.com/DaviMelooficial/triagem-fotos-medidor.git
cd triagem-fotos-medidor
uv sync
ollama pull qwen3-vl:4b
```

### 3. Subir o serviço

```bash
just serve
# ou, sem just:
uv run bentoml serve service:TriagemMedidor
```

O serviço fica em <http://localhost:3000>. Se a porta 3000 estiver ocupada: `just serve 3001`
(ou `uv run bentoml serve service:TriagemMedidor --port 3001`) e troque a porta nos comandos abaixo.

Se o Ollama estiver fechado ou o modelo não tiver sido baixado, o serviço **não sobe** e mostra o comando que resolve, por exemplo:

```
RuntimeError: Modelo qwen3-vl:4b não encontrado. Rode: ollama pull qwen3-vl:4b
```

### 4. Primeira predição (em outro terminal)

```bash
just demo
# ou, sem just (Mac/Linux):
curl -X POST http://localhost:3000/extrair -F "foto=@exemplos/01_display_registro_103.jpg"
# Windows (PowerShell): use curl.exe para não cair no alias do PowerShell
curl.exe -X POST http://localhost:3000/extrair -F "foto=@exemplos/01_display_registro_103.jpg"
```

Para testar outra foto: `just demo exemplos/02_display_registro_03.jpg` (ou `03_tampa_opaca.jpg`, `04_tampa_suja.jpg`).

## Contrato da API

**`POST /extrair`** — `multipart/form-data` com um campo **`foto`** (arquivo JPEG ou PNG).

```bash
curl -X POST http://localhost:3000/extrair -F "foto=@exemplos/01_display_registro_103.jpg"
```

Resposta real (HTTP 200) dessa chamada:

```json
{
    "numero_medidor": "3223400069",
    "funcao": "103",
    "leitura": "09888",
    "confianca": {
        "numero_medidor": 1.0,
        "funcao": 1.0,
        "leitura": 1.0,
        "geral": 1.0
    },
    "precisa_revisao": false,
    "modelo": "qwen3-vl:4b",
    "respostas_brutas": [
        {"numero_medidor": "3223400069", "funcao": "103", "leitura": "09888"},
        {"numero_medidor": "3223400069", "funcao": "103", "leitura": "09888"},
        {"numero_medidor": "3223400069", "funcao": "103", "leitura": "09888"}
    ],
    "tempo_segundos": 4.21
}
```

| campo | significado |
|---|---|
| `numero_medidor` | número de série da etiqueta (texto: preserva zeros à esquerda e letras); `null` se não visível |
| `funcao` | código do registro no display (ex. `"03"`) ou `"kWh"`/`"kvarh"` no medidor de rolete |
| `leitura` | só os dígitos do registro de consumo, como texto |
| `confianca` | fração de respostas que concordaram em cada campo; `geral` é a menor delas |
| `precisa_revisao` | `true` se `geral < 1.0` ou se algum campo veio `null` |
| `modelo` | modelo do Ollama que respondeu |
| `respostas_brutas` | as N respostas individuais usadas na votação (ajudam a entender a confiança) |
| `tempo_segundos` | tempo gasto nas N chamadas ao modelo |

**Caso de erro:** enviar algo que não é imagem devolve **HTTP 400**.

```bash
echo "isto nao e uma foto" > leitura.txt
curl -i -X POST http://localhost:3000/extrair -F "foto=@leitura.txt"
```

Resposta real:

```
HTTP/1.1 400 Bad Request
[{"error":"O arquivo enviado não é uma imagem válida. Envie uma foto JPEG ou PNG."}]
```

O 400 vem de `service.py`: o Pillow tenta ler o arquivo; se falhar, levantamos `bentoml.exceptions.InvalidArgument`,
que o BentoML traduz para "erro de quem enviou" (400), e não "erro do servidor" (500).

## Swagger

Com o serviço rodando, abra <http://localhost:3000> no navegador. Em **POST /extrair** clique em
*Try it out*, escolha um arquivo de `exemplos/` no campo `foto` e clique em *Execute*.

## Qual modelo e por quê

Usamos o **Qwen3-VL 4B** (`qwen3-vl:4b`), um modelo de visão e linguagem **pré-treinado** da equipe Qwen (Alibaba),
publicado com pesos abertos sob licença **Apache 2.0**, e servido localmente pelo **Ollama** (quantização Q4_K_M, ~3,3 GB).
Não fizemos treino nem ajuste fino: o modelo é usado como está, guiado pelo prompt em `prompt.txt`.

Comparamos com o **Qwen2.5-VL 3B** (`qwen2.5vl:3b`, da mesma equipe). Atenção à licença: no Hugging Face o
Qwen2.5-VL-3B-Instruct é publicado sob a **Qwen Research License** (`license_name: qwen-research`), que não permite uso
comercial sem licença da Alibaba (a página do Ollama mostra Apache 2.0, mas vale o card oficial). Já o
Qwen3-VL-4B-Instruct é **Apache 2.0** no Hugging Face. O Qwen3-VL 4B ganhou nas
duas coisas que importam aqui, acerto e tempo (tabela abaixo), então virou o padrão.

Motivos da escolha de um modelo pequeno e local: roda em notebook comum, não manda foto de cliente para nenhuma API
externa e não tem custo por chamada. O custo disso é acurácia (ver Limitações).

Para trocar de modelo sem mexer no código: `OLLAMA_MODELO=qwen2.5vl:3b just serve`
(PowerShell: `$env:OLLAMA_MODELO="qwen2.5vl:3b"; uv run bentoml serve service:TriagemMedidor`). Veja `.env.example`.

## Como a confiança é calculada

Um modelo de linguagem não devolve uma probabilidade confiável para "este número está certo". Usamos então
**autoconsistência**: fazemos a **mesma pergunta 3 vezes** (`N_RESPOSTAS`, padrão 3), com temperatura 0,6
(um pouco de variação) e uma *seed* diferente em cada vez. Se o modelo enxerga bem o número, ele repete a mesma
resposta; se está chutando, as respostas divergem.

Para cada campo:

- valor final = o mais votado entre as 3 respostas;
- confiança = votos do mais votado ÷ 3 → **1.0** (3 de 3), **0.67** (2 de 3) ou **0.33** (todas diferentes).

A **confiança geral** é a menor das três (a foto é tão confiável quanto o seu campo mais duvidoso), e
`precisa_revisao = confianca_geral < 1.0 ou algum campo null`.

Exemplo: respostas de leitura `052817`, `052817`, `052819` → leitura `052817`, confiança 0.67, vai para revisão.
Os testes em `tests/test_votacao.py` mostram essas contas sem precisar do modelo.

Empate: se as 3 respostas forem diferentes, vence a primeira (a da seed 1), com confiança 0.33, e a foto vai para
revisão de qualquer jeito. Por isso **N precisa ser ímpar** (3, 5...): com N par, um 2 a 2 seria decidido pela ordem, não por voto. O serviço se recusa a subir com N par ou zero.

Ponto importante: **unanimidade não é garantia de acerto** (ver Resultados). A confiança separa as fotos
em "certamente duvidosa" e "talvez boa", não em "certamente certa".

## Resultados da avaliação

Avaliamos com `avaliar.py` numa **amostra de 40 fotos reais de campo, não versionadas** (sorteio com seed fixa,
só fotos em que o leiturista registrou leitura sem ocorrência). Comparamos com o que o leiturista anotou:
número do medidor (ignorando zeros à esquerda) e leitura (como inteiro). Fotos 360×480, JPEG.
Máquina: Mac Apple Silicon (M5, 24 GB), Ollama 0.32.7, N = 3 respostas por foto.

| métrica | `qwen3-vl:4b` (padrão) | `qwen2.5vl:3b` |
|---|---|---|
| acerto do número do medidor | 25% (10/40) | 17,5% (7/40) |
| acerto da leitura | 10% (4/40) | 10% (4/40) |
| acerto dos dois campos | **10% (4/40)** | 2,5% (1/40) |
| fotos marcadas para revisão | 95% (38/40) | 100% (40/40) |
| confiança média quando acerta os dois | 0.59 | 0.67 |
| confiança média quando erra | 0.47 | 0.36 |
| fotos liberadas sem revisão | 2 (ambas erradas) | 0 |
| tempo médio por foto (3 chamadas) | **7,8 a 15,7 s**¹ | 11,9 s |

¹ A mesma amostra, com as mesmas respostas, levou 7,8 s numa execução e 15,7 s em outra, na mesma máquina: o tempo depende da carga do computador. O `qwen2.5vl:3b` foi medido uma vez só, então a comparação de tempo entre os dois é indicativa; a de acerto, não muda entre execuções.

Antes de ajustar o prompt para separar a função da leitura no display digital, o acerto nos dois campos na mesma
amostra era 4/40 (qwen3-vl) e 2/40 (qwen2.5-vl). Depois do ajuste: qwen3-vl continuou em 4/40 e qwen2.5-vl foi para
1/40. Essa diferença de uma foto está dentro do ruído de uma amostra de 40. O ajuste corrigiu esse erro numa foto de campo conferida à mão
(e hoje o teste da foto 01 confere a separação função `103` / leitura `09888`), mas a maior parte dos erros de campo tem outra causa (abaixo).

Leitura honesta: **com modelos de 3–4 bilhões de parâmetros e fotos de 360×480, o sistema não substitui o leiturista**.
Ele funciona como triagem conservadora: quase tudo vai para revisão, e a confiança média é maior quando acerta do que
quando erra (o sinal existe, mas é fraco).

### Fotos da equipe (`exemplos/`)

As 4 fotos de `exemplos/` foram tiradas pela equipe dos próprios medidores. Gabarito em `exemplos/controle_equipe.csv`
(`NA` = campo ilegível na foto, fica fora da conta). Saída real com `qwen3-vl:4b`:

| foto | o que mostra | resposta | confiança geral | revisão? |
|---|---|---|---|---|
| `01_display_registro_103.jpg` | display nítido, registro 103 | `3223400069` / `103` / `09888` — correto, 3 de 3 iguais | 1.0 | não |
| `02_display_registro_03.jpg` | mesmo medidor, registro 03 | `3223400069` / `03` / `17106` — correto, 3 de 3 iguais | 1.0 | não |
| `03_tampa_opaca.jpg` | tampa opaca, nada legível | leitura `000799`; uma das respostas foi `0123456789` (alucinação) | 0.33 | **sim** |
| `04_tampa_suja.jpg` | tampa suja, display ilegível | número `3202114430` (certo); leitura `006`, divergente nas 3 respostas | 0.33 | **sim** |

`just avaliar exemplos exemplos/controle_equipe.csv 4` resume: número 3/3, leitura 2/2, os dois campos 2/2,
revisão 2/4, de ~4 a ~9 s por foto conforme a carga da máquina. As duas legíveis saem unânimes e corretas; as duas ruins vão para revisão.

Por que as fotos da equipe são lidas tão melhor que as de campo? Uma explicação provável é a **resolução**: elas
têm 900×1600 e 1200×1600, contra 360×480 das fotos de campo avaliadas (cerca de 8 a 11 vezes mais pixels), e foram
tiradas de perto e com calma. Com só 4 fotos isso é uma indicação, não uma medida.

Tempo do `/extrair` medido com `curl` nas 4 fotos de `exemplos/` (modelo já carregado): de ~3,4 s a ~9,3 s por foto em execuções diferentes (o tempo varia com a carga da máquina).

## Limitações e propostas de melhoria

Padrões de erro que observamos nas fotos reais:

1. **Foto de longe**: o medidor ocupa um pedaço pequeno da imagem 360×480; o display e a etiqueta ficam com poucos pixels.
2. **Tampa de policarbonato suja, embaçada ou com reflexo do sol**: o display some atrás do brilho.
3. **Display LCD apagado ou com pouco contraste**: não há o que ler; o modelo deveria devolver `null`, mas às vezes inventa.
4. **Alucinação de sequência**: quando não enxerga, o modelo pequeno devolve coisas como `000000`, `012345`, `8888888`
   ou `1023456789`. Com 3 respostas diferentes isso cai na revisão, mas às vezes ele repete o mesmo chute.
5. **Unanimidade errada**: em 2 fotos o `qwen3-vl:4b` deu a mesma resposta nas 3 vezes (confiança 1.0), a foto foi
   liberada sem revisão e estava errada: numa, dois dígitos errados no número de série e a leitura também errada; na outra, o número lido não é o
   do cadastro e a leitura difere da anotada em 1.
6. **Confundir campos**: pegar o nome do modelo do equipamento impresso na tampa como número de série, ou juntar a
   função à leitura no display (corrigido no prompt).
7. **Medidor eletromecânico redondo** (ponteiros ou roletes pequenos sob cúpula de vidro): registros minúsculos.
8. **Número do medidor no cadastro diferente do impresso**: algumas linhas do controle têm dois números (`A/B`)
   ou um número que não aparece na foto; parte do "erro" é do gabarito, não do modelo.

Testamos ampliar a foto 2× antes de enviar ao modelo: não mudou o acerto em 15 fotos, então descartamos.

Limitação do serviço: se o modelo devolver um JSON inválido (fora do schema), a API responde **500**, sem tentar de novo.

Formatos aceitos: só JPEG e PNG. Outra imagem (WebP, TIFF...) recebe 400 com "Formato ... não aceito".

Propostas, da mais barata para a mais cara:

- **Recusar na hora a foto ruim**: medir nitidez e brilho no app de campo e pedir outra foto antes de o leiturista sair.
- **Recortar antes de ler**: um detector leve localiza display e etiqueta, e só o recorte vai para o modelo de visão.
- **Juntar com o histórico**: leitura menor que a anterior, ou consumo fora do padrão do cliente, vai para revisão
  mesmo com confiança 1.0. Isso ataca a "unanimidade errada".
- **Modelo maior** (ex. Qwen2.5-VL 7B ou 32B) numa máquina com GPU, medindo o ganho com o mesmo `avaliar.py`.
- **Ajuste fino** com as fotos já revisadas por humanos, que viram dado rotulado de graça.
- **Avaliar com amostra maior** (ex. 300 fotos) para ter intervalo de confiança apertado; 40 fotos dão uma ideia, não uma medida firme.

## Testes

```bash
just testar
# ou, sem just:
uv run pytest -v
```

**Precisam do Ollama aberto e do `qwen3-vl:4b` baixado.** O `tests/conftest.py` sobe o serviço de verdade
(`bentoml serve` na porta 3141) e os testes mandam fotos por HTTP, igual a um `curl`:

| teste | o que confere |
|---|---|
| `test_display_registro_103` | foto 01: número, função `103`, leitura `09888` (zero à esquerda) e formato da resposta |
| `test_display_registro_03` | foto 02: número, função `03`, leitura `17106` |
| `test_tampa_opaca_vai_para_revisao` | foto 03: `precisa_revisao = true` e confiança geral < 1 |
| `test_tampa_suja_vai_para_revisao` | foto 04: `precisa_revisao = true` e confiança geral < 1 |
| `test_arquivo_que_nao_e_imagem_devolve_400` | arquivo texto: HTTP 400 com mensagem clara |
| `test_imagem_webp_devolve_400` | imagem WebP (formato não aceito): HTTP 400 |
| `tests/test_votacao.py` (3 testes) | a conta da confiança (1.0, 0.67, 0.33), sem chamar o modelo |

Saída esperada: `9 passed` em ~20–40 s. Os testes de API usam seed fixa, mas o resultado de um modelo pode variar
de uma máquina para outra (CPU vs GPU); se um teste de leitura falhar em outra máquina, rode `just demo` com a mesma
foto e compare a resposta.

As imagens de `exemplos/` são **fotos tiradas pela equipe dos próprios medidores**. O `exemplos/controle_equipe.csv`
é o gabarito delas, no mesmo formato do controle de campo
(`Numero do medidor;Posicao do medidor lida;Nota de Leitura Atual;Foto do medidor`, separador `;`, `NA` = sem valor).

Para rodar a avaliação num lote próprio (fotos + CSV nesse formato):

```bash
just avaliar /caminho/das/fotos /caminho/do/controle.csv 40
# ou: uv run python avaliar.py --pasta /caminho/das/fotos --csv /caminho/do/controle.csv --n 40 --modelo qwen3-vl:4b
# com as fotos da equipe:
just avaliar exemplos exemplos/controle_equipe.csv 4
```

Campo com `NA` no gabarito fica fora da conta daquele campo. Ele imprime só agregados; o detalhe por foto vai para `resultados_locais/`, que o git ignora.

## Privacidade dos dados

- As fotos de `exemplos/` são da equipe, tiradas dos próprios medidores, e foram publicadas sem metadados (sem EXIF,
  sem localização). Nenhuma foto do cliente nem linha do controle de campo está neste repositório: as 40 fotos de
  campo da avaliação ficaram só na máquina de quem rodou o script; aqui estão apenas os números agregados.
- O `.gitignore` foi o **primeiro commit**, antes de qualquer código, e bloqueia `dados/`, `resultados_locais/`, `.env` etc.
- `just privacidade` procura termos que identificariam a origem dos dados nos arquivos versionados, no conteúdo de
  todos os commits e nas mensagens de commit, e procura fotos fora de `exemplos/` em qualquer commit; falha se encontrar.
- O modelo roda localmente no Ollama: a foto enviada ao serviço não sai da máquina.

## Uso de IA

**Ferramenta:** Claude Code, com o modelo Claude Opus 5.5.

**O que pedimos:** montar o repositório do zero com a arquitetura que definimos (BentoML → Ollama → JSON), código
simples e comentado em português, o cálculo de confiança por votação, imagens de exemplo, os testes, o script de
avaliação, rodar a avaliação com os dois modelos e escrever este README.

**Avaliação crítica (o que aconteceu de verdade):**

- **Funcionou de primeira:** a estrutura do serviço, o erro 400 e a votação. A IA conferiu no código instalado do
  BentoML 1.4.39 como o upload chega (um `Path` para um arquivo temporário) em vez de supor.
- **Precisou corrigir:**
  - O `qwen3-vl:4b` devolvia resposta vazia. A causa: é um modelo que "pensa" antes de responder e gastava ~100 s
    por foto nisso; com `think=False` o Ollama 0.32 passou a devolver o JSON no campo `thinking` em vez de `content`.
    Ficou um comentário no `extrator.py` explicando o contorno.
  - O prompt inicial tinha exemplos de número de série reais do formato pedido; o modelo pequeno **copiou o exemplo
    como resposta** numa foto real. Trocamos por uma descrição do formato, sem valores.
  - O modelo às vezes escreve `"null"` como texto em vez do null do JSON; isso virava um "voto" válido. Normalizamos.
  - No display digital o modelo juntava a função à leitura (`03052817`) **com confiança 1.0**. Ajustamos o prompt
    e isso nos mostrou que unanimidade não é acerto.
  - A porta 3000 estava ocupada na máquina de desenvolvimento; a receita `serve` ganhou um parâmetro de porta.
- **Decisão da equipe:** na primeira versão a IA desenhou imagens de exemplo com Pillow (medidores de mentira). A equipe
  trocou por fotos próprias dos medidores, que mostram o problema real (inclusive tampa opaca e suja) e permitem
  testar com gabarito conhecido; os testes e o `avaliar.py` foram ajustados para isso.
- **O que a IA não resolve:** a acurácia baixa nas fotos reais é limite do modelo pequeno e da qualidade das fotos,
  não do código. Os números da avaliação foram gerados executando o script, não estimados.
- **Responsabilidade da equipe:** o código foi mantido simples de propósito (poucos arquivos, funções curtas,
  comentários explicando o porquê) para que a equipe revise e saiba explicar cada linha; a revisão final e a
  responsabilidade pela entrega são da equipe.

## Licença

[MIT](LICENSE) © 2026 Davi Melo, Elizabete Albuquerque, Ivan Edward.

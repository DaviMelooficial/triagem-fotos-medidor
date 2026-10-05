"""Testes da votação: não chamam o modelo, só conferem a conta da confiança."""

from extrator import votar


def test_tres_respostas_iguais_dao_confianca_1():
    resposta = {"numero_medidor": "4071835526", "funcao": "03", "leitura": "052817"}
    valores, confianca = votar([resposta, resposta, resposta])
    assert valores == resposta
    assert confianca == {"numero_medidor": 1.0, "funcao": 1.0, "leitura": 1.0, "geral": 1.0}


def test_dois_contra_um_ganha_a_maioria_com_067():
    respostas = [
        {"numero_medidor": "4071835526", "funcao": "03", "leitura": "052817"},
        {"numero_medidor": "4071835526", "funcao": "03", "leitura": "052817"},
        {"numero_medidor": "4071835526", "funcao": "03", "leitura": "052819"},
    ]
    valores, confianca = votar(respostas)
    assert valores["leitura"] == "052817"
    assert confianca["leitura"] == 0.67
    # A confiança geral é a do campo mais duvidoso.
    assert confianca["geral"] == 0.67


def test_tudo_diferente_da_033():
    respostas = [{"numero_medidor": None, "funcao": None, "leitura": str(i)} for i in range(3)]
    _, confianca = votar(respostas)
    assert confianca["leitura"] == 0.33
    assert confianca["numero_medidor"] == 1.0  # três "null" iguais também é unanimidade
    assert confianca["geral"] == 0.33

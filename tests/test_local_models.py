"""Catálogo/hardware de modelos locais (local_models.py): função pura, sem
Flask — a classificação verde/amarelo/vermelho é o que orienta o usuário no
`helena setup`/`helena models`, então tem que ser previsível dado o hardware."""
import local_models as lm


def _model(params_b, est_gb):
    return {"name": "x", "label": "X", "params_b": params_b, "est_gb": est_gb}


def test_modelo_pequeno_cabe_folgado_com_pouca_ram():
    hw = {"ram_gb": 16.0, "cpu_count": 8, "gpu_vram_gb": None}
    assert lm.rate_model(_model(1.5, 1.0), hw) == "green"


def test_modelo_justo_fica_amarelo():
    # usable_capacity ~= min(16-4, 16*0.6) = 9.6GB; need = 7+1.5 = 8.5 -> não <= 70% (6.72) mas <= 9.6
    hw = {"ram_gb": 16.0, "cpu_count": 8, "gpu_vram_gb": None}
    assert lm.rate_model(_model(7, 7.0), hw) == "yellow"


def test_modelo_nao_cabe_fica_vermelho():
    hw = {"ram_gb": 8.0, "cpu_count": 4, "gpu_vram_gb": None}
    assert lm.rate_model(_model(32, 20.0), hw) == "red"


def test_sem_gpu_modelo_grande_nunca_fica_verde_mesmo_cabendo_em_ram():
    # RAM gigante (256GB) cobriria um 14B tranquilamente, mas sem GPU o CPU
    # puro é impraticável nesse tamanho — não pode virar 'green'.
    hw = {"ram_gb": 256.0, "cpu_count": 32, "gpu_vram_gb": None}
    assert lm.rate_model(_model(14, 9.0), hw) == "yellow"


def test_com_gpu_capacidade_e_a_vram_nao_a_ram():
    hw = {"ram_gb": 8.0, "cpu_count": 4, "gpu_vram_gb": 24.0}
    # RAM sozinha (8GB) reprovaria, mas a VRAM (24GB) da GPU é o que importa
    assert lm.rate_model(_model(14, 9.0), hw) == "green"


def test_gpu_grande_permite_modelo_acima_do_teto_cpu_only():
    hw = {"ram_gb": 32.0, "cpu_count": 16, "gpu_vram_gb": 48.0}
    assert lm.rate_model(_model(32, 20.0), hw) == "green"


def test_catalogo_ordenado_por_potencia_crescente():
    params = [m["params_b"] for m in lm.CATALOG]
    assert params == sorted(params)


def test_catalogo_nomes_unicos():
    names = [m["name"] for m in lm.CATALOG]
    assert len(names) == len(set(names))


def test_find_model_existente_e_inexistente():
    assert lm.find_model(lm.CATALOG[0]["name"]) == lm.CATALOG[0]
    assert lm.find_model("nao-existe:0b") is None


def test_usable_capacity_com_gpu_ignora_ram():
    hw = {"ram_gb": 4.0, "gpu_vram_gb": 12.0}
    assert lm.usable_capacity_gb(hw) == 12.0


def test_usable_capacity_sem_gpu_deixa_folga_pro_so():
    hw = {"ram_gb": 16.0, "gpu_vram_gb": None}
    # min(16-4, 16*0.6) = min(12, 9.6) = 9.6
    assert lm.usable_capacity_gb(hw) == 9.6

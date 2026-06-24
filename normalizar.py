import re

# ── NORMALIZAÇÃO DE PLACA ────────────────────────────────────────
def normalizar_placa(texto):
    """
    Aceita qualquer formato de placa e retorna padronizado sem hífen.
    Exemplos:
        QRH-1I14  → QRH1I14
        qrh 1i14  → QRH1I14
        PPW-3444  → PPW3444
        PPW3444   → PPW3444
        ABC-1234  → ABC1234  (Mercosul e antiga)
    """
    if not texto:
        return None
    # Remove tudo que não for letra ou número
    placa = re.sub(r'[^A-Za-z0-9]', '', texto).upper()
    # Valida tamanho mínimo e máximo
    if len(placa) < 7 or len(placa) > 8:
        return None
    return placa


# ── NORMALIZAÇÃO DE VALORES NUMÉRICOS ───────────────────────────
def normalizar_numero(texto):
    """
    Converte string de número pra float, aceitando vírgula ou ponto.
    Exemplos:
        "1.234,56" → 1234.56
        "1,234.56" → 1234.56
        "416.30"   → 416.30
        "416,30"   → 416.30
        "  59.557 " → 59.557
    """
    if texto is None:
        return None
    texto = str(texto).strip()
    if not texto:
        return None
    # Remove espaços internos
    texto = texto.replace(' ', '')
    # Se tem vírgula e ponto — descobre qual é decimal
    if ',' in texto and '.' in texto:
        if texto.rfind(',') > texto.rfind('.'):
            # vírgula é decimal: 1.234,56
            texto = texto.replace('.', '').replace(',', '.')
        else:
            # ponto é decimal: 1,234.56
            texto = texto.replace(',', '')
    elif ',' in texto:
        # só vírgula — é decimal brasileiro
        texto = texto.replace(',', '.')
    try:
        return float(texto)
    except ValueError:
        return None


# ── NORMALIZAÇÃO DE DATA ─────────────────────────────────────────
def normalizar_data(texto):
    """
    Converte vários formatos de data pra YYYY-MM-DD.
    Exemplos:
        "2026-05-06T10:46:00-03:00" → "2026-05-06"
        "06/05/2026"                → "2026-05-06"
        "06-05-2026"                → "2026-05-06"
        "2026/05/06"                → "2026-05-06"
        "06.05.2026"                → "2026-05-06"
    """
    if not texto:
        return None
    texto = str(texto).strip()

    # ISO com timezone — pega só a data
    m = re.match(r'^(\d{4}-\d{2}-\d{2})', texto)
    if m:
        return m.group(1)

    # DD/MM/YYYY ou DD-MM-YYYY ou DD.MM.YYYY
    m = re.match(r'^(\d{2})[/\-\.](\d{2})[/\-\.](\d{4})$', texto)
    if m:
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

    # YYYY/MM/DD
    m = re.match(r'^(\d{4})[/\-\.](\d{2})[/\-\.](\d{2})$', texto)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    return None


# ── NORMALIZAÇÃO DE KM ───────────────────────────────────────────
def normalizar_km(texto):
    """
    Extrai número inteiro de KM de vários formatos.
    Exemplos:
        "74634"    → 74634
        "74.634"   → 74634
        "74,634"   → 74634
        "KM: 74634" → 74634
        "  74634 " → 74634
    """
    if not texto:
        return None
    texto = str(texto).strip()
    # Remove prefixo "KM:" se houver
    texto = re.sub(r'^KM\s*:?\s*', '', texto, flags=re.IGNORECASE)
    # Remove separadores de milhar
    texto = re.sub(r'[.,](?=\d{3}(?!\d))', '', texto)
    # Remove tudo que não for dígito
    texto = re.sub(r'[^\d]', '', texto)
    try:
        return int(texto) if texto else None
    except ValueError:
        return None


# ── NORMALIZAÇÃO DE TEXTO GERAL ──────────────────────────────────
def normalizar_texto(texto, maiusculo=True, max_len=None):
    """
    Limpa e normaliza strings de texto.
    Remove espaços extras, caracteres de controle.
    """
    if not texto:
        return None
    texto = str(texto).strip()
    # Remove caracteres de controle
    texto = re.sub(r'[\x00-\x1f\x7f]', '', texto)
    # Colapsa espaços múltiplos
    texto = re.sub(r'\s+', ' ', texto)
    if maiusculo:
        texto = texto.upper()
    if max_len:
        texto = texto[:max_len]
    return texto or None


# ── DETECÇÃO DE TIPO DE NF ───────────────────────────────────────
KEYWORDS_COMBUSTIVEL = [
    'DIESEL', 'GASOLINA', 'ETANOL', 'ARLA', 'COMBUSTIVEL',
    'GNV', 'BIODIESEL', 'S10', 'S500', 'ALCOOL'
]

def detectar_tipo_nf(produto):
    """
    Retorna 'abastecimento' ou 'manutencao' baseado no produto.
    """
    if not produto:
        return 'manutencao'
    produto_upper = produto.upper()
    for kw in KEYWORDS_COMBUSTIVEL:
        if kw in produto_upper:
            return 'abastecimento'
    return 'manutencao'


# ── EXTRAÇÃO DE PLACA DO infCpl ──────────────────────────────────
def extrair_placa_infcpl(infcpl):
    """
    Tenta extrair placa de campos de observação com vários padrões.
    Exemplos de infCpl reais:
        "PLACA: PPW3444"
        "PLACA:QRH-1I14"
        "PLACA PPW-3444"
        "VEICULO: HILUX PLACA: PPW3444 KM: 43352"
        "VEIC.:ATEGO 1719, PLACA:QRH-1I14, KM: 74634"
    """
    if not infcpl:
        return None
    padroes = [
        r'PLACA\s*:?\s*([A-Z]{3}[-\s]?\d[A-Z0-9]\d{2})',   # Mercosul
        r'PLACA\s*:?\s*([A-Z]{3}[-\s]?\d{4})',               # Antiga
    ]
    for p in padroes:
        m = re.search(p, infcpl.upper())
        if m:
            return normalizar_placa(m.group(1))
    return None


# ── EXTRAÇÃO DE KM DO infCpl ─────────────────────────────────────
def extrair_km_infcpl(infcpl):
    """
    Extrai KM do campo de observação.
    Exemplos:
        "KM: 74634"
        "KM:  74.634"
        "KM 74634,"
    """
    if not infcpl:
        return None
    m = re.search(r'KM\s*:?\s*([\d\s.,]+?)(?:\s*[,;]|\s+[A-Z]|$)', infcpl.upper())
    if m:
        return normalizar_km(m.group(1))
    return None


# ── EXTRAÇÃO DE MOTORISTA DO infCpl ─────────────────────────────
def extrair_motorista_infcpl(infcpl):
    """
    Extrai motorista do campo de observação.
    Exemplos:
        "MOTORISTA: NILTON"
        "MOTORISTA: ALEX COELHO"
        "AUTORIZADO POR NILTON"
    """
    if not infcpl:
        return None

    padroes = [
        r'MOTORISTA\s*:?\s*([A-Z][A-Z\s]+?)(?:\s*[,;]|\s+VENDEDOR|\s+KM|\s*$)',
        r'AUTORIZADO\s+POR\s+([A-Z][A-Z\s]+?)(?:\s*[,;]|\s+VALOR|\s*$)',
    ]
    for p in padroes:
        m = re.search(p, infcpl.upper())
        if m:
            return normalizar_texto(m.group(1), max_len=100)
    return None
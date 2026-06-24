import re
import io
import pdfplumber
from normalizar import (
    normalizar_placa, normalizar_numero, normalizar_data,
    normalizar_km, normalizar_texto, detectar_tipo_nf,
    extrair_placa_infcpl, extrair_km_infcpl, extrair_motorista_infcpl
)

# ── DETECÇÃO DE TIPO DE PDF ──────────────────────────────────────
def detectar_tipo_pdf(texto):
    texto_upper = texto.upper()
    if 'REDE DE POSTOS SAGUI' in texto_upper and 'TOTAL DA FATURA' in texto_upper:
        return 'fatura_sagui'
    if 'POSTO PARADAO' in texto_upper or 'POSTO PARADA' in texto_upper:
        return 'cupom_paradao'
    if 'DANFE' in texto_upper or 'NF-E' in texto_upper or 'NOTA FISCAL' in texto_upper:
        return 'nfe_simples'
    return 'desconhecido'


# ── LEITOR PRINCIPAL ─────────────────────────────────────────────
def ler_pdf(pdf_bytes):
    try:
        with pdfplumber.open(pdf_bytes) as pdf:
            texto_completo = '\n'.join(
                page.extract_text() or '' for page in pdf.pages
            )
    except Exception as e:
        raise ValueError(f'Erro ao abrir PDF: {str(e)}')

    if not texto_completo.strip():
        return ler_pdf_ocr(pdf_bytes)

    tipo_pdf = detectar_tipo_pdf(texto_completo)

    if tipo_pdf == 'fatura_sagui':
        return ler_fatura_sagui(texto_completo)
    elif tipo_pdf == 'cupom_paradao':
        return ler_cupom_paradao(texto_completo)
    elif tipo_pdf == 'nfe_simples':
        return ler_nfe_simples(pdf_bytes, texto_completo)
    else:
        return ler_generico(texto_completo)


# ── NF-e SIMPLES — ABORDAGEM GENÉRICA ───────────────────────────
def ler_nfe_simples(pdf_bytes, texto):
    """
    Extrai dados de DANFE/NF-e usando combinação de:
    1. Regex flexíveis para campos de cabeçalho
    2. Extração de tabela pelo pdfplumber para linha de produto
    3. Fallback por posição de texto para valores numéricos
    """

    # ── Número da NF ──
    # Aceita: Nº 000.006.084, No 6084, N° 6084
    m = re.search(r'N[ºo°°]\s*[\.:]*\s*0*(\d+)', texto, re.IGNORECASE)
    numero_nf = m.group(1) if m else None

    # ── Data ──
    # Tenta DATA DE EMISSÃO primeiro, depois qualquer data no texto
    m = re.search(r'DATA\s+DE\s+EMISS[AÃ]O\s*[\.:]*\s*(\d{2}/\d{2}/\d{4})', texto, re.IGNORECASE)
    if not m:
        m = re.search(r'EMISS[AÃ]O[:\s]*(\d{2}/\d{2}/\d{4})', texto, re.IGNORECASE)
    if not m:
        # Pega a primeira data que aparecer
        m = re.search(r'(\d{2}/\d{2}/\d{4})', texto)
    data = normalizar_data(m.group(1)) if m else None

    # ── Emitente ──
    # Primeira linha não vazia geralmente é o emitente
    linhas = [l.strip() for l in texto.split('\n') if l.strip()]
    posto_nome = normalizar_texto(linhas[0], max_len=100) if linhas else None

    # ── Valor total da nota ──
    # Tenta múltiplos padrões em ordem de confiabilidade
    valor_total = None
    padroes_valor = [
        r'VALOR\s+TOTAL\s+DA\s+NOTA\s*[\.:]*\s*([\d.,]+)',
        r'VALOR\s+TOTAL\s+DOS\s+PRODUTOS\s*[\.:]*\s*([\d.,]+)',
        r'VALOR\s+TOTAL[:\s]*([\d.,]+)',
        r'TOTAL[:\s]*R?\$?\s*([\d.,]+)',
    ]
    for p in padroes_valor:
        m = re.search(p, texto, re.IGNORECASE)
        if m:
            valor_total = normalizar_numero(m.group(1))
            if valor_total and valor_total > 0:
                break

    # ── Placa, KM, Motorista ──
    placa     = extrair_placa_infcpl(texto)
    km        = extrair_km_infcpl(texto)
    motorista = extrair_motorista_infcpl(texto)

    # ── Produto, Litros, Valor Unitário — via tabela pdfplumber ──
    produto    = None
    litros     = None
    valor_unit = None

    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes) if isinstance(pdf_bytes, bytes) else pdf_bytes) as pdf:
            for page in pdf.pages:
                tabelas = page.extract_tables()
                for tabela in tabelas:
                    for linha in tabela:
                        if not linha:
                            continue
                        linha_texto = ' '.join(str(c) for c in linha if c)
                        linha_upper = linha_texto.upper()

                        # Linha de produto contém combustível ou produto relevante
                        if any(kw in linha_upper for kw in ['DIESEL', 'GASOLINA', 'ETANOL', 'OLEO', 'ARLA']):
                            # Extrai células não nulas
                            celulas = [str(c).strip() for c in linha if c and str(c).strip()]

                            # Produto é a célula com mais texto
                            for c in celulas:
                                if len(c) > 5 and any(kw in c.upper() for kw in ['DIESEL', 'GASOLINA', 'ETANOL', 'OLEO', 'ARLA']):
                                    produto = normalizar_texto(c, max_len=100)
                                    break

                            # Busca números — litros geralmente é o maior número fracionado
                            numeros = []
                            for c in celulas:
                                n = normalizar_numero(c)
                                if n and n > 0:
                                    numeros.append(n)

                            if numeros:
                                # Litros: número > 1 e < 2000 (volume razoável)
                                candidatos_litros = [n for n in numeros if 1 < n < 2000]
                                if candidatos_litros:
                                    litros = min(candidatos_litros)  # menor = litros, maior = total

                                # Valor unitário: número entre 3 e 20 (preço combustível)
                                candidatos_unit = [n for n in numeros if 3 < n < 20]
                                if candidatos_unit:
                                    valor_unit = candidatos_unit[0]

                            break
                    if produto:
                        break
    except Exception:
        pass

    # Fallback produto via texto se tabela falhou
    if not produto:
        padroes_prod = [
            r'(OLEO DIESEL[^\n]+)',
            r'(GASOLINA[^\n]+)',
            r'(ETANOL[^\n]+)',
            r'(ARLA[^\n]+)',
            r'DESCRI[CÇ][AÃ]O DO PRODUTO[^\n]*\n([^\n]+)',
        ]
        for p in padroes_prod:
            m = re.search(p, texto, re.IGNORECASE)
            if m:
                produto = normalizar_texto(m.group(1), max_len=100)
                break

    # Fallback litros via texto
    if not litros:
        # Padrão: número seguido de L (ex: 59,557 L ou 59.557L)
        m = re.search(r'(\d+[.,]\d+)\s*L(?:\s|$|\n)', texto)
        if m:
            litros = normalizar_numero(m.group(1))

    tipo = detectar_tipo_nf(produto)

    return [{
        'data': data,
        'numero_nf': numero_nf,
        'posto_nome': posto_nome,
        'produto': produto,
        'litros': litros,
        'valor_unitario': valor_unit,
        'valor_total': valor_total,
        'placa': placa,
        'km': km,
        'motorista': motorista,
        'tipo': tipo,
        'multi_item': False,
        'origem': 'pdf_nfe',
    }]


# ── FATURA CONSOLIDADA SAGUI ─────────────────────────────────────
def ler_fatura_sagui(texto):
    itens = []

    m = re.search(r'FATURA\s*#\s*(\d+)', texto, re.IGNORECASE)
    numero_nf = m.group(1) if m else None
    posto_nome = 'REDE DE POSTOS SAGUI LTDA'

    blocos = re.split(r'Placa:\s*', texto, flags=re.IGNORECASE)

    for bloco in blocos[1:]:
        linhas = bloco.strip().split('\n')
        if not linhas:
            continue

        placa = normalizar_placa(linhas[0].strip().split()[0])
        if not placa:
            continue

        docs = re.finditer(
            r'Doc:\s*(\d+)\s+Data:\s*(\d{2}/\d{2}/\d{4})\s+(?:Und:\s*\d+\s+)?Km:\s*([\d]+)',
            bloco, re.IGNORECASE
        )

        for doc in docs:
            data    = normalizar_data(doc.group(2))
            km      = normalizar_km(doc.group(3))
            num_doc = doc.group(1)

            pos = doc.end()
            trecho = bloco[pos:pos+300]

            m_prod = re.search(
                r'([A-Z][A-Z0-9 ]+?)\s+([\d.,]+)\s+([\d.,]+)\s+[\d.,]+\s+([\d.,]+)',
                trecho
            )

            if m_prod:
                produto     = normalizar_texto(m_prod.group(1), max_len=100)
                litros      = normalizar_numero(m_prod.group(2))
                valor_unit  = normalizar_numero(m_prod.group(3))
                valor_total = normalizar_numero(m_prod.group(4))
            else:
                produto = litros = valor_unit = valor_total = None

            itens.append({
                'data': data,
                'numero_nf': f"{numero_nf}-{num_doc}" if numero_nf else num_doc,
                'posto_nome': posto_nome,
                'produto': produto,
                'litros': litros,
                'valor_unitario': valor_unit,
                'valor_total': valor_total,
                'placa': placa,
                'km': km,
                'motorista': None,
                'tipo': detectar_tipo_nf(produto),
                'multi_item': True,
                'origem': 'pdf_sagui',
            })

    return itens if itens else ler_generico(texto)


# ── CUPOM PARADÃO ────────────────────────────────────────────────
def ler_cupom_paradao(texto):
    posto_nome = 'POSTO PARADAO DA 101 LTDA'

    m = re.search(r'(\d{2}/\d{2}/\d{4})', texto)
    data = normalizar_data(m.group(1)) if m else None

    placa = extrair_placa_infcpl(texto)

    m = re.search(r'Valor\s*[\.:]*\s*([\d.,]+)', texto, re.IGNORECASE)
    valor_total = normalizar_numero(m.group(1)) if m else None

    m = re.search(r'(DIESEL[^\n]*|GASOLINA[^\n]*|ETANOL[^\n]*)\s+([\d.,]+)\s*L', texto, re.IGNORECASE)
    produto = normalizar_texto(m.group(1), max_len=100) if m else None
    litros  = normalizar_numero(m.group(2)) if m else None

    motorista = extrair_motorista_infcpl(texto)

    m = re.search(r'Ref\s+Cupon\s*[\.:]*\s*(\d+)', texto, re.IGNORECASE)
    numero_nf = m.group(1) if m else None

    return [{
        'data': data,
        'numero_nf': numero_nf,
        'posto_nome': posto_nome,
        'produto': produto,
        'litros': litros,
        'valor_unitario': None,
        'valor_total': valor_total,
        'placa': placa,
        'km': None,
        'motorista': motorista,
        'tipo': detectar_tipo_nf(produto),
        'multi_item': False,
        'origem': 'pdf_paradao',
    }]


# ── EXTRAÇÃO GENÉRICA ────────────────────────────────────────────
def ler_generico(texto):
    placa     = extrair_placa_infcpl(texto)
    km        = extrair_km_infcpl(texto)
    motorista = extrair_motorista_infcpl(texto)

    m = re.search(r'(\d{2}/\d{2}/\d{4})', texto)
    data = normalizar_data(m.group(1)) if m else None

    m = re.search(r'TOTAL\s*[\.:]*\s*R?\$?\s*([\d.,]+)', texto, re.IGNORECASE)
    valor_total = normalizar_numero(m.group(1)) if m else None

    return [{
        'data': data,
        'numero_nf': None,
        'posto_nome': None,
        'produto': None,
        'litros': None,
        'valor_unitario': None,
        'valor_total': valor_total,
        'placa': placa,
        'km': km,
        'motorista': motorista,
        'tipo': 'abastecimento',
        'multi_item': False,
        'origem': 'pdf_generico',
    }]


# ── OCR PARA PDF ESCANEADO ───────────────────────────────────────
def ler_pdf_ocr(pdf_bytes):
    try:
        import pytesseract
        pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

        with pdfplumber.open(io.BytesIO(pdf_bytes) if isinstance(pdf_bytes, bytes) else pdf_bytes) as pdf:
            textos = []
            for page in pdf.pages:
                img = page.to_image(resolution=300).original
                texto = pytesseract.image_to_string(img, lang='por')
                textos.append(texto)

        texto_completo = '\n'.join(textos)
        tipo_pdf = detectar_tipo_pdf(texto_completo)

        if tipo_pdf == 'cupom_paradao':
            return ler_cupom_paradao(texto_completo)
        else:
            return ler_generico(texto_completo)

    except ImportError:
        raise ValueError('pytesseract não instalado — PDF escaneado não suportado.')
    except Exception as e:
        raise ValueError(f'Erro no OCR: {str(e)}')
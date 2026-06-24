from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from flask_wtf import CSRFProtect
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor
import os
from datetime import timedelta, date
from collections import defaultdict
import time
import re
from lxml import etree
from normalizar import (
    normalizar_placa, normalizar_numero, normalizar_data,
    normalizar_km, normalizar_texto, detectar_tipo_nf,
    extrair_placa_infcpl, extrair_km_infcpl, extrair_motorista_infcpl
)

load_dotenv()  

app = Flask(__name__)

@app.template_filter('enumerate_items')
def enumerate_items(seq):
    return enumerate(seq)

app.secret_key = os.getenv('SECRET_KEY')
app.permanent_session_lifetime = timedelta(hours=8)

csrf = CSRFProtect(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

tentativas = defaultdict(list)

def check_rate_limit(ip):
    agora = time.time()
    tentativas[ip] = [t for t in tentativas[ip] if agora - t < 300]
    if len(tentativas[ip]) >= 5:
        return False
    tentativas[ip].append(agora)
    return True

class User(UserMixin):
    def __init__(self, id, username):
        self.id = id
        self.username = username

@login_manager.user_loader
def load_user(user_id):
    if user_id == 'admin':
        return User('admin', os.getenv('ADMIN_USERNAME'))
    return None

def get_db():
    return psycopg2.connect(
        host=os.getenv('DB_HOST'),
        database=os.getenv('DB_NAME'),
        user=os.getenv('DB_USER'),
        password=os.getenv('DB_PASSWORD'),
        port=os.getenv('DB_PORT')
    )

# ── PARSER DE XML NF-e ──────────────────────────────────────────
def parse_nfe_xml(xml_bytes):
    """
    Retorna uma lista de itens extraídos da NF-e.
    Cada NF pode ter múltiplos produtos (det), todos são retornados.
    Campos compartilhados (data, numero_nf, posto, placa, km, motorista)
    são herdados do cabeçalho da NF.
    """
    ns = {'nfe': 'http://www.portalfiscal.inf.br/nfe'}
    tree = etree.fromstring(xml_bytes)

    def get(path):
        el = tree.find(path, ns)
        return el.text.strip() if el is not None and el.text else None

    # Campos do cabeçalho — compartilhados entre todos os itens
    dhEmi      = get('.//nfe:dhEmi')
    numero_nf  = get('.//nfe:nNF')
    posto_nome = normalizar_texto(get('.//nfe:emit/nfe:xNome'), max_len=100)
    data       = normalizar_data(dhEmi)
    v_total_nf = normalizar_numero(get('.//nfe:vNF'))

    # infCpl — placa, km e motorista ficam aqui no cabeçalho
    infcpl    = get('.//nfe:infCpl') or ''
    placa     = extrair_placa_infcpl(infcpl)
    km        = extrair_km_infcpl(infcpl)
    motorista = extrair_motorista_infcpl(infcpl)

    # Itera sobre todos os itens da NF
    itens = tree.findall('.//nfe:det', ns)
    resultados = []

    for det in itens:
        def get_det(path):
            el = det.find(path, ns)
            return el.text.strip() if el is not None and el.text else None

        produto = normalizar_texto(get_det('nfe:prod/nfe:xProd'), max_len=100)
        litros  = normalizar_numero(get_det('nfe:prod/nfe:qCom'))
        v_unit  = normalizar_numero(get_det('nfe:prod/nfe:vUnCom'))
        v_prod  = normalizar_numero(get_det('nfe:prod/nfe:vProd'))
        tipo    = detectar_tipo_nf(produto)

        # Se NF tem só um item, usa vNF como valor total
        # Se tem múltiplos, usa vProd de cada item
        valor_item = v_prod if len(itens) > 1 else v_total_nf

        resultados.append({
            'data': data,
            'numero_nf': numero_nf,
            'posto_nome': posto_nome,
            'produto': produto,
            'litros': litros,
            'valor_unitario': v_unit,
            'valor_total': valor_item,
            'placa': placa,
            'km': km,
            'motorista': motorista,
            'tipo': tipo,
            'multi_item': len(itens) > 1,
        })

    return resultados

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('index'))
    erro = None
    if request.method == 'POST':
        ip = request.remote_addr
        if not check_rate_limit(ip):
            erro = 'Muitas tentativas. Aguarde 5 minutos.'
            return render_template('login.html', erro=erro)
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        if username == os.getenv('ADMIN_USERNAME') and password == os.getenv('ADMIN_PASSWORD'):
            user = User('admin', username)
            login_user(user, remember=False)
            session.permanent = True
            return redirect(url_for('index'))
        else:
            time.sleep(1)
            erro = 'Usuário ou senha incorretos.'
    return render_template('login.html', erro=erro)

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    data_inicio = request.args.get('data_inicio', '2025-12-01')
    data_fim = request.args.get('data_fim', date.today().isoformat())

    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT COUNT(*) as total_abastecimentos,
               COALESCE(SUM(valor_total), 0) as total_mes,
               COALESCE(SUM(litros), 0) as total_litros
        FROM abastecimentos
        WHERE data BETWEEN %s AND %s
    """, (data_inicio, data_fim))
    kpis = cursor.fetchone()

    cursor.execute("""
        SELECT COUNT(*) as total_manutencoes
        FROM manutencao
        WHERE data BETWEEN %s AND %s
    """, (data_inicio, data_fim))
    manutencoes = cursor.fetchone()

    cursor.execute("""
        SELECT a.data, a.placa, a.motorista, p.nome as posto,
               a.produto, a.litros, a.valor_total
        FROM abastecimentos a
        JOIN postos p ON p.id_posto = a.id_posto
        WHERE a.data BETWEEN %s AND %s
        ORDER BY a.data DESC LIMIT 10
    """, (data_inicio, data_fim))
    ultimos = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('index.html',
        total_mes=f"{kpis['total_mes']:.2f}",
        total_litros=f"{kpis['total_litros']:.2f}",
        total_abastecimentos=kpis['total_abastecimentos'],
        total_manutencoes=manutencoes['total_manutencoes'],
        data_inicio=data_inicio,
        data_fim=data_fim,
        ultimos_abastecimentos=ultimos
    )

@app.route('/veiculo/<placa>')
@login_required
def veiculo(placa):
    placa = ''.join(c for c in placa.upper() if c.isalnum())
    if not placa:
        return redirect(url_for('index'))

    data_inicio = request.args.get('data_inicio', '2025-12-01')
    data_fim = request.args.get('data_fim', date.today().isoformat())

    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("SELECT * FROM veiculos WHERE placa = %s", (placa,))
    veiculo_info = cursor.fetchone()
    if not veiculo_info:
        cursor.close()
        conn.close()
        return redirect(url_for('index'))

    cursor.execute("""
        SELECT a.data, a.motorista, p.nome as posto, a.produto,
               a.litros, a.valor_unitario, a.valor_total, a.km
        FROM abastecimentos a
        JOIN postos p ON p.id_posto = a.id_posto
        WHERE a.placa = %s AND a.data BETWEEN %s AND %s
        ORDER BY a.data DESC
    """, (placa, data_inicio, data_fim))
    abastecimentos = cursor.fetchall()

    cursor.execute("""
        SELECT data, descricao, valor, fornecedor FROM manutencao
        WHERE placa = %s AND data BETWEEN %s AND %s ORDER BY data DESC
    """, (placa, data_inicio, data_fim))
    manutencoes = cursor.fetchall()

    cursor.execute("""
        SELECT TO_CHAR(data, 'YYYY-MM') as mes, SUM(valor_total) as custo
        FROM abastecimentos
        WHERE placa = %s AND data BETWEEN %s AND %s
        GROUP BY TO_CHAR(data, 'YYYY-MM') ORDER BY mes
    """, (placa, data_inicio, data_fim))
    grafico_dados = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('veiculo.html',
        veiculo=veiculo_info, abastecimentos=abastecimentos, manutencoes=manutencoes,
        grafico_labels=[r['mes'] for r in grafico_dados],
        grafico_valores=[float(r['custo']) for r in grafico_dados],
        data_inicio=data_inicio, data_fim=data_fim,
        total_abastecido=sum(r['valor_total'] for r in abastecimentos),
        total_manutencao=sum(r['valor'] for r in manutencoes)
    )
@app.route('/veiculos')
@login_required
def veiculos():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    cursor.execute("""
        SELECT v.placa, v.modelo, v.tipo_combustivel, v.motorista_padrao,
               COALESCE(SUM(a.valor_total), 0) as total_gasto,
               COUNT(a.id_abastecimento) as total_abastecimentos
        FROM veiculos v
        LEFT JOIN abastecimentos a ON a.placa = v.placa
        WHERE v.placa NOT IN ('ENTREGA', 'BUJAO')
        GROUP BY v.placa, v.modelo, v.tipo_combustivel, v.motorista_padrao
        ORDER BY v.placa
    """)
    lista_veiculos = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template('veiculos.html', veiculos=lista_veiculos)


@app.route('/veiculo/<placa>/excluir', methods=['POST'])
@login_required
def excluir_veiculo(placa):
    placa = ''.join(c for c in placa.upper() if c.isalnum())
    if not placa:
        return redirect(url_for('veiculos'))

    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    try:
        # Verifica se tem registros vinculados
        cursor.execute("SELECT COUNT(*) as total FROM abastecimentos WHERE placa = %s", (placa,))
        tem_abastecimento = cursor.fetchone()['total'] > 0

        cursor.execute("SELECT COUNT(*) as total FROM manutencao WHERE placa = %s", (placa,))
        tem_manutencao = cursor.fetchone()['total'] > 0

        if tem_abastecimento or tem_manutencao:
            flash(f'Não é possível excluir {placa} — existem abastecimentos ou manutenções vinculados.', 'error')
        else:
            cursor.execute("DELETE FROM veiculos WHERE placa = %s", (placa,))
            conn.commit()
            flash(f'Veículo {placa} excluído com sucesso.', 'success')

    except Exception as e:
        conn.rollback()
        flash(f'Erro ao excluir: {str(e)}', 'error')

    cursor.close()
    conn.close()

    return redirect(url_for('veiculos'))

@app.route('/relatorios')
@login_required
def relatorios():
    data_inicio = request.args.get('data_inicio', '2025-12-01')
    data_fim = request.args.get('data_fim', date.today().isoformat())
 
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)
 
    # Totais gerais
    cursor.execute("""
        SELECT COALESCE(SUM(valor_total), 0) as total_combustivel,
               COALESCE(SUM(litros), 0) as total_litros,
               COUNT(*) as total_abastecimentos
        FROM abastecimentos WHERE data BETWEEN %s AND %s
    """, (data_inicio, data_fim))
    totais = cursor.fetchone()
 
    cursor.execute("""
        SELECT COALESCE(SUM(valor), 0) as total_manutencao
        FROM manutencao WHERE data BETWEEN %s AND %s
    """, (data_inicio, data_fim))
    total_man = cursor.fetchone()
 
    # Top 3 veículos por abastecimento
    cursor.execute("""
        SELECT placa, SUM(valor_total) as total FROM abastecimentos
        WHERE data BETWEEN %s AND %s AND placa NOT IN ('ENTREGA', 'BUJAO')
        GROUP BY placa ORDER BY total DESC LIMIT 3
    """, (data_inicio, data_fim))
    top_abastecimento = cursor.fetchall()
 
    # Top 3 veículos por manutenção
    cursor.execute("""
        SELECT placa, SUM(valor) as total FROM manutencao
        WHERE data BETWEEN %s AND %s GROUP BY placa ORDER BY total DESC LIMIT 3
    """, (data_inicio, data_fim))
    top_manutencao = cursor.fetchall()
 
    # Top 3 postos
    cursor.execute("""
        SELECT p.nome as posto, SUM(a.valor_total) as total
        FROM abastecimentos a JOIN postos p ON p.id_posto = a.id_posto
        WHERE a.data BETWEEN %s AND %s GROUP BY p.nome ORDER BY total DESC LIMIT 3
    """, (data_inicio, data_fim))
    top_postos = cursor.fetchall()
 
    # ── Gráfico: custo por produto (cards no topo) ──
    cursor.execute("""
        SELECT produto, SUM(valor_total) as total
        FROM abastecimentos
        WHERE data BETWEEN %s AND %s AND produto IS NOT NULL
        GROUP BY produto ORDER BY total DESC LIMIT 4
    """, (data_inicio, data_fim))
    custo_produto = cursor.fetchall()
 
    # ── Gráfico: custo por mês (barras verticais) ──
    cursor.execute("""
        SELECT TO_CHAR(data, 'YYYY-MM') as mes, SUM(valor_total) as total
        FROM abastecimentos
        WHERE data BETWEEN %s AND %s
        GROUP BY TO_CHAR(data, 'YYYY-MM') ORDER BY mes
    """, (data_inicio, data_fim))
    custo_mes = cursor.fetchall()
 
    # ── Gráfico: custo por posto (barras horizontais) ──
    cursor.execute("""
        SELECT p.nome as posto, SUM(a.valor_total) as total
        FROM abastecimentos a JOIN postos p ON p.id_posto = a.id_posto
        WHERE a.data BETWEEN %s AND %s
        GROUP BY p.nome ORDER BY total DESC
    """, (data_inicio, data_fim))
    grafico_postos = cursor.fetchall()
 
    # ── Gráfico: custo por motorista (barras horizontais) ──
    cursor.execute("""
        SELECT motorista, SUM(valor_total) as total
        FROM abastecimentos
        WHERE data BETWEEN %s AND %s AND motorista IS NOT NULL AND motorista != ''
        GROUP BY motorista ORDER BY total DESC LIMIT 8
    """, (data_inicio, data_fim))
    grafico_motoristas = cursor.fetchall()
 
    cursor.close()
    conn.close()
 
    meses_nomes = {1:'Jan',2:'Fev',3:'Mar',4:'Abr',5:'Mai',6:'Jun',
                   7:'Jul',8:'Ago',9:'Set',10:'Out',11:'Nov',12:'Dez'}
 
    labels_mes = []
    for r in custo_mes:
        ano, mes = r['mes'].split('-')
        labels_mes.append(f"{meses_nomes[int(mes)]} {ano}")
 
    return render_template('relatorios.html',
        data_inicio=data_inicio, data_fim=data_fim,
        total_combustivel=float(totais['total_combustivel']),
        total_litros=float(totais['total_litros']),
        total_abastecimentos=totais['total_abastecimentos'],
        total_manutencao=float(total_man['total_manutencao']),
        top_abastecimento=top_abastecimento,
        top_manutencao=top_manutencao,
        top_postos=top_postos,
        custo_produto=custo_produto,
        grafico_mes_labels=labels_mes,
        grafico_mes_valores=[float(r['total']) for r in custo_mes],
        grafico_postos_labels=[r['posto'] for r in grafico_postos],
        grafico_postos_valores=[float(r['total']) for r in grafico_postos],
        grafico_motoristas_labels=[r['motorista'] for r in grafico_motoristas],
        grafico_motoristas_valores=[float(r['total']) for r in grafico_motoristas],
    )


from leitor_pdf import ler_pdf

# ── UPLOAD DE NF-e XML e PDF ─────────────────────────────────────
@app.route('/upload', methods=['GET', 'POST'])
@login_required
def upload():
    resultados = []
    if request.method == 'POST':
        arquivos = request.files.getlist('arquivos')
        for arquivo in arquivos:
            nome = arquivo.filename.lower()
            try:
                conteudo = arquivo.read()

                # Valida magic bytes
                is_pdf = conteudo[:4] == b'%PDF'
                is_xml = conteudo[:100].lstrip().startswith((b'<?xml', b'<nfeProc', b'<NFe'))

                if nome.endswith('.pdf') and not is_pdf:
                    resultados.append({'arquivo': arquivo.filename, 'itens': [], 'erro': 'Arquivo não é um PDF válido'})
                    continue

                if nome.endswith('.xml') and not is_xml:
                    resultados.append({'arquivo': arquivo.filename, 'itens': [], 'erro': 'Arquivo não é um XML válido'})
                    continue

                if nome.endswith('.xml'):
                    itens = parse_nfe_xml(conteudo)
                elif nome.endswith('.pdf'):
                    import io
                    itens = ler_pdf(io.BytesIO(conteudo))
                else:
                    resultados.append({'arquivo': arquivo.filename, 'itens': [], 'erro': 'Formato inválido — apenas XML ou PDF'})
                    continue

                resultados.append({
                    'arquivo': arquivo.filename,
                    'itens': itens,
                    'erro': None
                })

            except Exception as e:
                resultados.append({
                    'arquivo': arquivo.filename,
                    'itens': [],
                    'erro': str(e)
                })

        return render_template('upload.html', resultados=resultados)
    return render_template('upload.html', resultados=[])

@app.route('/upload/confirmar', methods=['POST'])
@login_required
def upload_confirmar():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    total   = int(request.form.get('total', 0))
    salvos  = 0
    pulados = 0

    for i in range(total):
        try:
            tipo      = request.form.get(f'tipo_{i}', 'abastecimento')
            if tipo == 'ignorar':
                continue

            numero_nf   = request.form.get(f'numero_nf_{i}', '').strip()
            item_idx    = request.form.get(f'item_idx_{i}', '0')
            placa       = normalizar_placa(request.form.get(f'placa_{i}', ''))
            data        = request.form.get(f'data_{i}')
            motorista   = normalizar_texto(request.form.get(f'motorista_{i}', ''), max_len=100)
            produto     = normalizar_texto(request.form.get(f'produto_{i}', ''), max_len=100)
            valor_total = normalizar_numero(request.form.get(f'valor_total_{i}'))
            posto_nome  = normalizar_texto(request.form.get(f'posto_nome_{i}', ''), max_len=100)

            if not placa or not data or not valor_total:
                flash(f'Registro {i+1} ignorado — placa, data ou valor ausente.', 'warning')
                continue

            # Verifica duplicata — usa numero_nf + item_idx pra NFs com múltiplos itens
            chave_nf = f"{numero_nf}-{item_idx}" if numero_nf else None
            if chave_nf:
                cursor.execute(
                    "SELECT id FROM notas_fiscais WHERE numero_nf = %s", (chave_nf,)
                )
                if cursor.fetchone():
                    flash(f'NF {numero_nf} item {item_idx} já existe — ignorado.', 'warning')
                    pulados += 1
                    continue

            # Insere na notas_fiscais
            cursor.execute("""
                INSERT INTO notas_fiscais (numero_nf, data, valor_total, tipo)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (chave_nf, data, valor_total, tipo))
            id_nota = cursor.fetchone()['id']

            if tipo == 'abastecimento':
                litros     = normalizar_numero(request.form.get(f'litros_{i}'))
                valor_unit = normalizar_numero(request.form.get(f'valor_unitario_{i}'))
                km         = normalizar_km(request.form.get(f'km_{i}'))

                cursor.execute("SELECT id_posto FROM postos WHERE nome = %s", (posto_nome,))
                posto = cursor.fetchone()
                if posto:
                    id_posto = posto['id_posto']
                else:
                    cursor.execute(
                        "INSERT INTO postos (nome, endereco) VALUES (%s, %s) RETURNING id_posto",
                        (posto_nome, 'A preencher')
                    )
                    id_posto = cursor.fetchone()['id_posto']

                cursor.execute("""
                    INSERT INTO abastecimentos
                        (id_nota, data, motorista, placa, id_posto, produto,
                         litros, valor_unitario, valor_total, km)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (id_nota, data, motorista, placa, id_posto, produto,
                      litros, valor_unit, valor_total, km))
            else:
                cursor.execute("""
                    INSERT INTO manutencao (id_nota, data, placa, descricao, valor, fornecedor)
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (id_nota, data, placa, produto, valor_total, posto_nome))

            salvos += 1
            conn.commit()

        except Exception as e:
            conn.rollback()
            flash(f'Erro ao salvar registro {i+1}: {str(e)}', 'error')
            continue

    cursor.close()
    conn.close()

    if salvos:
        flash(f'✅ {salvos} registro(s) salvo(s) com sucesso!', 'success')
    if pulados:
        flash(f'⚠️ {pulados} item(ns) já existiam no banco e foram ignorados.', 'warning')

    return redirect(url_for('index'))

 

@app.route('/veiculo/novo', methods=['GET', 'POST'])
@login_required
def veiculo_novo():
    if request.method == 'POST':
        placa             = normalizar_placa(request.form.get('placa', ''))
        modelo            = normalizar_texto(request.form.get('modelo', ''), max_len=100)
        tipo_combustivel  = normalizar_texto(request.form.get('tipo_combustivel', ''), max_len=50)
        motorista_padrao  = normalizar_texto(request.form.get('motorista_padrao', ''), max_len=100)

        if not placa or not modelo:
            flash('Placa e modelo são obrigatórios.', 'error')
            return render_template('veiculo_novo.html')

        conn = get_db()
        cursor = conn.cursor(cursor_factory=RealDictCursor)

        try:
            cursor.execute("SELECT placa FROM veiculos WHERE placa = %s", (placa,))
            if cursor.fetchone():
                flash(f'Veículo {placa} já está cadastrado.', 'warning')
            else:
                cursor.execute("""
                    INSERT INTO veiculos (placa, modelo, tipo_combustivel, motorista_padrao)
                    VALUES (%s, %s, %s, %s)
                """, (placa, modelo, tipo_combustivel or 'Desconhecido', motorista_padrao or 'A definir'))
                conn.commit()
                flash(f'✅ Veículo {placa} cadastrado com sucesso!', 'success')
                cursor.close()
                conn.close()
                return redirect(url_for('veiculos'))

        except Exception as e:
            conn.rollback()
            flash(f'Erro ao cadastrar: {str(e)}', 'error')

        cursor.close()
        conn.close()

    return render_template('veiculo_novo.html')
@app.route('/registro/manual', methods=['GET', 'POST'])
@login_required
def registro_manual():
    conn = get_db()
    cursor = conn.cursor(cursor_factory=RealDictCursor)

    # Busca veículos e postos para os selects
    cursor.execute("SELECT placa, modelo FROM veiculos ORDER BY placa")
    veiculos = cursor.fetchall()

    cursor.execute("SELECT id_posto, nome FROM postos ORDER BY nome")
    postos = cursor.fetchall()

    cursor.close()
    conn.close()

    if request.method == 'POST':
        tipo        = request.form.get('tipo', 'abastecimento')
        placa       = normalizar_placa(request.form.get('placa', ''))
        data        = request.form.get('data')
        motorista   = normalizar_texto(request.form.get('motorista', ''), max_len=100)
        produto     = normalizar_texto(request.form.get('produto', ''), max_len=100)
        valor_total = normalizar_numero(request.form.get('valor_total'))
        posto_nome  = normalizar_texto(request.form.get('posto_nome', ''), max_len=100)

        if not placa or not data or not valor_total:
            flash('Placa, data e valor total são obrigatórios.', 'error')
            return render_template('registro_manual.html', veiculos=veiculos, postos=postos)

        try:
            conn = get_db()
            cursor = conn.cursor(cursor_factory=RealDictCursor)

            if tipo == 'abastecimento':
                litros     = normalizar_numero(request.form.get('litros'))
                valor_unit = normalizar_numero(request.form.get('valor_unitario'))
                km         = normalizar_km(request.form.get('km'))

                cursor.execute("SELECT id_posto FROM postos WHERE nome = %s", (posto_nome,))
                posto = cursor.fetchone()
                if posto:
                    id_posto = posto['id_posto']
                else:
                    cursor.execute(
                        "INSERT INTO postos (nome, endereco) VALUES (%s, %s) RETURNING id_posto",
                        (posto_nome, 'A preencher')
                    )
                    id_posto = cursor.fetchone()['id_posto']

                cursor.execute("""
                    INSERT INTO abastecimentos
                        (data, motorista, placa, id_posto, produto,
                         litros, valor_unitario, valor_total, km)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (data, motorista, placa, id_posto, produto,
                      litros, valor_unit, valor_total, km))

            else:  # manutencao
                fornecedor = posto_nome
                descricao  = produto
                cursor.execute("""
                    INSERT INTO manutencao (data, placa, descricao, valor, fornecedor)
                    VALUES (%s, %s, %s, %s, %s)
                """, (data, placa, descricao, valor_total, fornecedor))

            conn.commit()
            cursor.close()
            conn.close()

            flash('✅ Registro salvo com sucesso!', 'success')
            return redirect(url_for('index'))

        except Exception as e:
            conn.rollback()
            flash(f'Erro ao salvar: {str(e)}', 'error')

    return render_template('registro_manual.html', veiculos=veiculos, postos=postos)

if __name__ == '__main__':
   app.run(debug=os.getenv('FLASK_DEBUG', 'False') == 'True')
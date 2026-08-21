from flask import Flask, render_template, request, redirect, session, jsonify, send_file
import os
import json
import urllib.request
import psycopg2
from psycopg2 import errors
from psycopg2.extras import RealDictCursor
from decimal import Decimal, InvalidOperation
from io import BytesIO
from datetime import datetime, timedelta
import qrcode

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "seven_store_versao_08")

def conectar_banco():
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL não configurada no Render.")
    return psycopg2.connect(database_url, cursor_factory=RealDictCursor)

def criar_banco():
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("CREATE TABLE IF NOT EXISTS usuarios (id SERIAL PRIMARY KEY, usuario VARCHAR(100) UNIQUE NOT NULL, senha VARCHAR(255) NOT NULL, perfil VARCHAR(20) NOT NULL DEFAULT 'usuario')")
            cursor.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS perfil VARCHAR(20) NOT NULL DEFAULT 'usuario'")
            cursor.execute("CREATE TABLE IF NOT EXISTS integracoes (id SERIAL PRIMARY KEY, usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE, canal VARCHAR(30) NOT NULL, account_id VARCHAR(150), page_id VARCHAR(150), phone_number_id VARCHAR(150), business_account_id VARCHAR(150), UNIQUE(usuario_id, canal))")
            cursor.execute("CREATE TABLE IF NOT EXISTS conversas (id SERIAL PRIMARY KEY, usuario_id INTEGER REFERENCES usuarios(id) ON DELETE CASCADE, canal VARCHAR(30) NOT NULL, identificador VARCHAR(200) NOT NULL, nome VARCHAR(200), status VARCHAR(30) DEFAULT 'aberta', ultima_mensagem TEXT)")
            cursor.execute("CREATE TABLE IF NOT EXISTS mensagens (id SERIAL PRIMARY KEY, conversa_id INTEGER REFERENCES conversas(id) ON DELETE CASCADE, direcao VARCHAR(20) NOT NULL, texto TEXT NOT NULL, externa_id VARCHAR(200), criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS caixas (id SERIAL PRIMARY KEY, usuario_id INTEGER UNIQUE REFERENCES usuarios(id) ON DELETE CASCADE, nome VARCHAR(100) NOT NULL, status VARCHAR(20) NOT NULL DEFAULT 'aberto', saldo_inicial NUMERIC(12,2) NOT NULL DEFAULT 0, aberto_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP, fechado_em TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS vendas (id SERIAL PRIMARY KEY, usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL, caixa_id INTEGER REFERENCES caixas(id) ON DELETE SET NULL, total NUMERIC(12,2) NOT NULL, pagamento VARCHAR(30) NOT NULL, descricao VARCHAR(255), pix_chave VARCHAR(255), criada_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("CREATE TABLE IF NOT EXISTS configuracoes_loja (id INTEGER PRIMARY KEY DEFAULT 1, pix_chave VARCHAR(255), pix_nome VARCHAR(100), pix_cidade VARCHAR(100), atualizado_em TIMESTAMP DEFAULT CURRENT_TIMESTAMP)")
            cursor.execute("INSERT INTO configuracoes_loja (id) VALUES (1) ON CONFLICT (id) DO NOTHING")
            cursor.execute("SELECT COUNT(*) AS total FROM usuarios")
            if cursor.fetchone()["total"]:
                cursor.execute("SELECT COUNT(*) AS total FROM usuarios WHERE perfil='administrador'")
                if cursor.fetchone()["total"] == 0:
                    cursor.execute("UPDATE usuarios SET perfil='administrador' WHERE id=(SELECT id FROM usuarios ORDER BY id ASC LIMIT 1)")
                cursor.execute("UPDATE usuarios SET perfil='usuario' WHERE perfil IS NULL OR perfil=''")
        banco.commit()
    finally:
        banco.close()

criar_banco()

def usuario_atual():
    if "usuario_id" not in session: return None
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE id=%s", (session["usuario_id"],))
            return cursor.fetchone()
    finally: banco.close()

def usuario_id_atual():
    atual = usuario_atual(); return atual["id"] if atual else None

def exigir_login(): return "usuario_id" in session

def exigir_perfil(*perfis):
    atual = usuario_atual(); return atual and atual["perfil"] in perfis

def garantir_caixa(uid):
    banco = conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM caixas WHERE usuario_id=%s", (uid,)); caixa = cursor.fetchone()
            if not caixa:
                cursor.execute("INSERT INTO caixas (usuario_id,nome,status) VALUES (%s,%s,'aberto') RETURNING *", (uid, f"Caixa {uid}")); caixa = cursor.fetchone()
        banco.commit(); return caixa
    finally: banco.close()

def contexto_base():
    atual = usuario_atual()
    return {"usuario": atual["usuario"] if atual else "", "perfil": atual["perfil"] if atual else "", "perfil_nome": {"administrador":"Administrador","subadministrador":"Sub-administrador","usuario":"Usuário"}.get(atual["perfil"] if atual else "","")}

@app.route("/")
def login():
    if exigir_login(): return redirect("/inicio")
    return render_template("login.html")

@app.route("/login", methods=["POST"])
def fazer_login():
    usuario=request.form.get("usuario","").strip(); senha=request.form.get("senha","")
    if not usuario or not senha: return render_template("login.html", erro="Preencha usuário e senha.")
    banco=conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT * FROM usuarios WHERE usuario=%s AND senha=%s",(usuario,senha)); resultado=cursor.fetchone()
    finally: banco.close()
    if resultado:
        session["usuario_id"]=resultado["id"]; session["usuario"]=resultado["usuario"]; garantir_caixa(resultado["id"]); return redirect("/inicio")
    return render_template("login.html", erro="Usuário ou senha incorretos.")

@app.route("/cadastro")
def cadastro(): return render_template("cadastro.html")

@app.route("/cadastrar", methods=["POST"])
def cadastrar():
    usuario=request.form.get("usuario","").strip(); senha=request.form.get("senha","")
    if not usuario or not senha: return render_template("cadastro.html", erro="Preencha usuário e senha.")
    banco=conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM usuarios"); primeiro=cursor.fetchone()["total"]==0
            cursor.execute("INSERT INTO usuarios (usuario,senha,perfil) VALUES (%s,%s,%s) RETURNING id",(usuario,senha,"administrador" if primeiro else "usuario")); uid=cursor.fetchone()["id"]
        banco.commit(); garantir_caixa(uid)
    except errors.UniqueViolation:
        banco.rollback(); return render_template("cadastro.html", erro="Esse usuário já existe.")
    finally: banco.close()
    return redirect("/")

@app.route("/inicio")
def inicio():
    if not exigir_login(): return redirect("/")
    return render_template("inicio.html", **contexto_base())

def pagina_menu(titulo,descricao,*perfis):
    if not exigir_login(): return redirect("/")
    if perfis and not exigir_perfil(*perfis): return redirect("/inicio")
    return render_template("pagina.html", titulo=titulo, descricao=descricao, **contexto_base())

@app.route("/produtos")
def produtos(): return pagina_menu("Produtos","Aqui você poderá cadastrar e gerenciar os produtos.","administrador","subadministrador")
@app.route("/clientes")
def clientes(): return pagina_menu("Clientes","Aqui você poderá cadastrar e gerenciar os clientes.","administrador","subadministrador")
@app.route("/estoque")
def estoque(): return pagina_menu("Estoque","Aqui você poderá controlar o estoque da loja.","administrador","subadministrador")

@app.route("/relatorios")
def relatorios():
    if not exigir_login(): return redirect("/")
    if not exigir_perfil("administrador","subadministrador"): return redirect("/inicio")
    return render_template("relatorios.html", **contexto_base())

@app.route("/api/relatorios")
def api_relatorios():
    if not exigir_login() or not exigir_perfil("administrador","subadministrador"):
        return jsonify({"erro":"Acesso negado"}),403
    dias=request.args.get("dias",default=7,type=int)
    if dias not in (7,30,90): dias=7
    agora=datetime.now(); inicio=agora-timedelta(days=dias)
    banco=conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(total),0) AS total, COUNT(*) AS quantidade FROM vendas WHERE criada_em >= %s",(inicio,)); resumo=cursor.fetchone()
            cursor.execute("SELECT pagamento, COUNT(*) AS quantidade, COALESCE(SUM(total),0) AS total FROM vendas WHERE criada_em >= %s GROUP BY pagamento ORDER BY total DESC",(inicio,)); pagamentos=cursor.fetchall()
            cursor.execute("SELECT EXTRACT(HOUR FROM criada_em)::int AS hora, COUNT(*) AS quantidade, COALESCE(SUM(total),0) AS total FROM vendas WHERE criada_em >= %s GROUP BY hora ORDER BY hora",(inicio,)); horarios=cursor.fetchall()
            cursor.execute("SELECT c.id, c.nome, COALESCE(u.usuario,'Sem usuário') AS usuario, COUNT(v.id) AS quantidade, COALESCE(SUM(v.total),0) AS total FROM caixas c LEFT JOIN usuarios u ON u.id=c.usuario_id LEFT JOIN vendas v ON v.caixa_id=c.id AND v.criada_em >= %s GROUP BY c.id,c.nome,u.usuario ORDER BY total DESC, quantidade DESC",(inicio,)); ranking=cursor.fetchall()
            cursor.execute("SELECT TO_CHAR(criada_em,'DD/MM') AS dia, COUNT(*) AS quantidade, COALESCE(SUM(total),0) AS total FROM vendas WHERE criada_em >= %s GROUP BY DATE(criada_em),TO_CHAR(criada_em,'DD/MM') ORDER BY DATE(criada_em)",(inicio,)); por_dia=cursor.fetchall()
    finally: banco.close()
    def clean(rows):
        out=[]
        for row in rows:
            d=dict(row)
            for k,v in d.items():
                if isinstance(v,Decimal): d[k]=float(v)
            out.append(d)
        return out
    return jsonify({"dias":dias,"resumo":{"total":float(resumo["total"] or 0),"quantidade":int(resumo["quantidade"] or 0)},"pagamentos":clean(pagamentos),"horarios":clean(horarios),"ranking":clean(ranking),"por_dia":clean(por_dia)})

@app.route("/caixa")
def caixa():
    if not exigir_login(): return redirect("/")
    uid=usuario_id_atual(); meu_caixa=garantir_caixa(uid); banco=conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT COALESCE(SUM(total),0) AS total FROM vendas WHERE caixa_id=%s",(meu_caixa["id"],)); total=cursor.fetchone()["total"]
            cursor.execute("SELECT * FROM vendas WHERE caixa_id=%s ORDER BY criada_em DESC LIMIT 20",(meu_caixa["id"],)); vendas_recentes=cursor.fetchall()
    finally: banco.close()
    return render_template("pagina.html",titulo=f"Caixa - {meu_caixa['nome']}",descricao=f"Status: {meu_caixa['status']} | Total vendido: R$ {Decimal(total or 0):.2f}",vendas=vendas_recentes,**contexto_base())

@app.route("/vendas", methods=["GET","POST"])
def vendas():
    if not exigir_login(): return redirect("/")
    uid=usuario_id_atual(); caixa_atual=garantir_caixa(uid); mensagem=None; erro=None
    if request.method=="POST":
        try:
            total=Decimal(request.form.get("total","0").replace(",",".")); pagamento=request.form.get("pagamento","dinheiro").strip(); descricao=request.form.get("descricao","").strip()
            if total<=0: raise InvalidOperation
            banco=conectar_banco()
            try:
                with banco.cursor() as cursor:
                    cursor.execute("SELECT pix_chave FROM configuracoes_loja WHERE id=1"); loja=cursor.fetchone(); pix_chave=loja["pix_chave"] if loja else None
                    cursor.execute("INSERT INTO vendas (usuario_id,caixa_id,total,pagamento,descricao,pix_chave) VALUES (%s,%s,%s,%s,%s,%s)",(uid,caixa_atual["id"],total,pagamento,descricao,pix_chave if pagamento=="pix" else None))
                banco.commit()
            finally: banco.close()
            mensagem=f"Venda registrada: R$ {total:.2f}"
        except (InvalidOperation,ValueError): erro="Informe um valor de venda válido."
    banco=conectar_banco()
    try:
        with banco.cursor() as cursor:
            cursor.execute("SELECT pix_chave,pix_nome,pix_cidade FROM configuracoes_loja WHERE id=1"); loja=cursor.fetchone() or {}
            cursor.execute("SELECT * FROM vendas WHERE usuario_id=%s ORDER BY criada_em DESC LIMIT 20",(uid,)); vendas_recentes=cursor.fetchall()
    finally: banco.close()
    return render_template("vendas.html",**contexto_base(),caixa=caixa_atual,loja=loja,vendas=vendas_recentes,mensagem=mensagem,erro=erro)

@app.route("/pix/qr")
def pix_qr():
    if not exigir_login(): return redirect("/")
    chave=request.args.get("chave","").strip(); valor=request.args.get("valor","").strip(); nome=request.args.get("nome","Seven Store").strip()[:25]; cidade=request.args.get("cidade","BRASILIA").strip()[:15]
    if not chave:
        banco=conectar_banco()
        try:
            with banco.cursor() as cursor:
                cursor.execute("SELECT pix_chave,pix_nome,pix_cidade FROM configuracoes_loja WHERE id=1"); loja=cursor.fetchone() or {}
        finally: banco.close()
        chave=(loja.get("pix_chave") or "").strip(); nome=(loja.get("pix_nome") or nome).strip()[:25]; cidade=(loja.get("pix_cidade") or cidade).strip()[:15]
    if not chave: return "Chave Pix não cadastrada.",400
    payload=gerar_payload_pix(chave,valor,nome,cidade); imagem=qrcode.make(payload); arquivo=BytesIO(); imagem.save(arquivo,format="PNG"); arquivo.seek(0); return send_file(arquivo,mimetype="image/png")

def campo_pix(id_campo,valor): return f"{id_campo}{len(valor):02d}{valor}"
def gerar_payload_pix(chave,valor,nome,cidade):
    merchant=campo_pix("00","BR.GOV.BCB.PIX")+campo_pix("01",chave); valor_txt=""
    if valor:
        try: valor_txt=f"{Decimal(valor.replace(',','.')):.2f}"
        except Exception: valor_txt=""
    payload=campo_pix("00","01")+campo_pix("26",merchant)+campo_pix("52","0000")+campo_pix("53","986")
    if valor_txt: payload+=campo_pix("54",valor_txt)
    payload+=campo_pix("58","BR")+campo_pix("59",nome or "SEVEN STORE")+campo_pix("60",cidade or "BRASILIA")+campo_pix("62",campo_pix("05","SEVENSTORE")); base=payload+"6304"; crc=0xFFFF
    for byte in base.encode("utf-8"):
        crc^=byte<<8
        for _ in range(8): crc=((crc<<1)^0x1021)&0xFFFF if crc&0x8000 else (crc<<1)&0xFFFF
    return base+f"{crc:04X}"

@app.route("/atendimento")
def atendimento():
    if not exigir_login(): return redirect("/")
    return pagina_menu("Atendimento","Aqui você poderá acompanhar os atendimentos.","administrador","subadministrador")

@app.route("/sair")
def sair(): session.clear(); return redirect("/")

if __name__=="__main__": app.run(host="0.0.0.0",port=int(os.environ.get("PORT",5000)))

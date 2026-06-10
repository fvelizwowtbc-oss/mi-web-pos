from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
from datetime import datetime, timedelta, date
import sqlite3
from functools import wraps
import requests
import json
import os
import logging
from logging.handlers import RotatingFileHandler
from werkzeug.security import generate_password_hash, check_password_hash
import secrets
from PIL import Image
import uuid
from contextlib import contextmanager

# ============ CONFIGURACIÓN DE LOGGING ============
def setup_logging():
    if not os.path.exists('logs'):
        os.mkdir('logs')
    file_handler = RotatingFileHandler('logs/sistema_gestion.log', maxBytes=10240, backupCount=10, encoding='utf-8')
    file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    file_handler.setLevel(logging.INFO)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    console_handler.setLevel(logging.INFO)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

# ============ CONFIGURACIÓN DE LA APP ============
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
app.config['DATABASE_DIR'] = os.environ.get('DATABASE_DIR', 'databases')
app.config['UPLOAD_FOLDER'] = os.environ.get('UPLOAD_FOLDER', 'static/uploads')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(hours=1)

setup_logging()
for directorio in [app.config['DATABASE_DIR'], app.config['UPLOAD_FOLDER']]:
    if not os.path.exists(directorio): os.makedirs(directorio)

DATABASE_PRINCIPAL = os.path.join(app.config['DATABASE_DIR'], 'sistema_gestion.db')

# ============ CONTEXT MANAGERS ============
@contextmanager
def get_main_db():
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON'); conn.execute('PRAGMA journal_mode = WAL')
        yield conn; conn.commit()
    except Exception as e:
        if conn: conn.rollback()
        raise
    finally:
        if conn: conn.close()

@contextmanager
def get_user_db(user_id):
    conn = None
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON'); conn.execute('PRAGMA journal_mode = WAL')
        yield conn; conn.commit()
    except Exception as e:
        if conn: conn.rollback()
        raise
    finally:
        if conn: conn.close()

# ============ FUNCIONES DE IMÁGENES ============
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def generar_nombre_archivo_seguro(filename, user_id, producto_id=None):
    ext = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
    ts = datetime.now().strftime('%Y%m%d_%H%M%S'); rs = uuid.uuid4().hex[:8]
    base = f"user_{user_id}_prod_{producto_id}_{ts}_{rs}" if producto_id else f"user_{user_id}_{ts}_{rs}"
    return f"{base}.{ext}"

def guardar_imagen_producto(file, user_id, producto_id):
    if not file or file.filename == '' or not allowed_file(file.filename): return None
    try:
        filename = generar_nombre_archivo_seguro(file.filename, user_id, producto_id)
        user_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id))
        if not os.path.exists(user_dir): os.makedirs(user_dir)
        fp = os.path.join(user_dir, filename); file.save(fp)
        try:
            img = Image.open(fp); img.thumbnail((300, 300))
            img.save(os.path.join(user_dir, f"thumb_{filename}"), quality=85, optimize=True)
        except: pass
        return filename
    except: return None

def obtener_imagen_producto(user_id, producto_id):
    try:
        import glob
        user_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id))
        if not os.path.exists(user_dir): return None
        for img in glob.glob(os.path.join(user_dir, f"*user_{user_id}_prod_{producto_id}_*")):
            if 'thumb_' in os.path.basename(img): return os.path.basename(img)
        imgs = glob.glob(os.path.join(user_dir, f"*user_{user_id}_prod_{producto_id}_*"))
        return os.path.basename(imgs[0]) if imgs else None
    except: return None

def eliminar_imagenes_producto(user_id, producto_id):
    try:
        import glob
        for fp in glob.glob(os.path.join(app.config['UPLOAD_FOLDER'], str(user_id), f"*user_{user_id}_prod_{producto_id}_*")):
            try: os.remove(fp)
            except: pass
    except: pass

# ============ CATEGORÍAS Y MARCAS ============
def obtener_categorias_predefinidas():
    return ['Repuestos Moto','Electronica','Hogar','Ropa','Herramientas','Automotriz','Oficina','Alimentos','Bebidas','Juguetes','Deportes','Salud y Belleza','Libros','Construccion','Farmacia','Otros']

def obtener_subcategorias_por_categoria(cat):
    d = {'Repuestos Moto':['Motor','Transmision','Frenos','Suspension','Electricidad','Chasis','Accesorios','Llantas y Neumaticos','Carroceria','Escape','Combustible','Refrigeracion','Lubricacion','Faros y Luces','Asientos','Manillares','Otros Repuestos'],'Electronica':['Telefonos','Computadoras','Tablets','Audio','Video','Camaras','Accesorios'],'Automotriz':['Repuestos Auto','Accesorios Auto','Lubricantes','Llantas','Baterias'],'Herramientas':['Electricas','Manuales','Medicion','Jardineria']}
    return d.get(cat, [])

def obtener_marcas_moto():
    m = sorted(['Bera','Empire Keeway','Hero','Honda','Kawasaki','Suzuki','TVS','Yamaha']); m.append('Otra Marca'); return m

# ============ FUNCIONES DE BASE DE DATOS ============
def init_main_db():
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('''CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL, telefono TEXT, role TEXT DEFAULT 'user',
            fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP, activo BOOLEAN DEFAULT TRUE,
            es_vendedor BOOLEAN DEFAULT FALSE, usuario_padre_id INTEGER,
            limite_productos INTEGER DEFAULT 50, limite_ventas_mensuales INTEGER DEFAULT 20,
            tasa_activa TEXT DEFAULT 'oficial', suscripcion_activa BOOLEAN DEFAULT FALSE,
            fecha_fin_suscripcion DATE, FOREIGN KEY(usuario_padre_id) REFERENCES usuarios(id) ON DELETE SET NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS suscripciones (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER NOT NULL, plan TEXT NOT NULL,
            dias INTEGER NOT NULL, fecha_inicio DATE NOT NULL, fecha_fin DATE NOT NULL,
            monto REAL NOT NULL DEFAULT 0, estado TEXT DEFAULT 'activa', metodo_pago TEXT,
            transaccion_id TEXT, notas TEXT, creado_por INTEGER, cancelado_en DATE,
            cancelado_por INTEGER, creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE,
            FOREIGN KEY(creado_por) REFERENCES usuarios(id) ON DELETE SET NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS config_empresa (
            id INTEGER PRIMARY KEY AUTOINCREMENT, usuario_id INTEGER UNIQUE NOT NULL,
            nombre TEXT NOT NULL DEFAULT 'Mi Empresa', rif TEXT DEFAULT 'J-00000000-0',
            direccion TEXT DEFAULT 'Venezuela', telefono TEXT DEFAULT '', email TEXT DEFAULT '',
            logo TEXT DEFAULT '', mensaje_factura TEXT DEFAULT 'Gracias por su compra!',
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP, actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(usuario_id) REFERENCES usuarios(id) ON DELETE CASCADE)''')
        for col, tipo in [('suscripcion_activa','BOOLEAN DEFAULT FALSE'),('fecha_fin_suscripcion','DATE'),('limite_productos','INTEGER DEFAULT 50'),('limite_ventas_mensuales','INTEGER DEFAULT 20')]:
            try: conn.execute(f'ALTER TABLE usuarios ADD COLUMN {col} {tipo}')
            except: pass
        admin = conn.execute("SELECT id FROM usuarios WHERE email='admin@sistema.com'").fetchone()
        if not admin:
            conn.execute('INSERT INTO usuarios (nombre,email,password_hash,role,activo) VALUES (?,?,?,?,?)',
                       ('Administrador','admin@sistema.com',generate_password_hash('Admin123!'),'admin',1))
            conn.execute('INSERT INTO usuarios (nombre,email,password_hash,telefono,activo) VALUES (?,?,?,?,?)',
                       ('Usuario Demo','demo@demo.com',generate_password_hash('User123!'),'04141234567',1))
        conn.commit()
    except Exception as e: logging.error(f"Error BD: {e}")
    finally:
        if conn: conn.close()

def init_user_db(user_id):
    db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
    if os.path.exists(db_path): ensure_user_db_tables(user_id); return
    conn = None
    try:
        conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row; conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('''CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, modelo TEXT, marca TEXT,
            referencia TEXT, categoria TEXT, subcategoria TEXT, codigo TEXT UNIQUE NOT NULL,
            color TEXT, descripcion TEXT, fecha_expiracion DATE, ubicacion TEXT,
            stock_actual INTEGER DEFAULT 0, stock_minimo INTEGER DEFAULT 5,
            precio_costo_usd REAL DEFAULT 0, precio_venta_usd REAL NOT NULL,
            margen_ganancia REAL DEFAULT 0, impuesto REAL DEFAULT 16,
            proveedor_nombre TEXT, proveedor_contacto TEXT, proveedor_notas TEXT,
            pagado_contado BOOLEAN DEFAULT 1, dias_credito INTEGER DEFAULT 0,
            observaciones TEXT, imagen_principal TEXT,
            medida_unidad TEXT DEFAULT 'Unidad', medida_cantidad REAL DEFAULT 1,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP, actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS ventas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, producto_id INTEGER NOT NULL,
            vendedor_id INTEGER, cantidad INTEGER NOT NULL, precio_usd REAL NOT NULL,
            tasa_oficial REAL NOT NULL DEFAULT 36.00, tasa_manual1 REAL NOT NULL DEFAULT 38.00,
            tasa_manual2 REAL NOT NULL DEFAULT 40.00, tasa_usada TEXT NOT NULL DEFAULT 'oficial',
            total_usd REAL NOT NULL, total_oficial REAL NOT NULL, total_manual1 REAL NOT NULL,
            total_manual2 REAL NOT NULL, metodo_pago TEXT DEFAULT 'efectivo',
            observaciones TEXT, nro_factura TEXT, tipo_venta TEXT DEFAULT 'contado',
            cliente_id INTEGER, cliente_nombre TEXT DEFAULT 'Consumidor Final',
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(producto_id) REFERENCES productos(id) ON DELETE RESTRICT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS clientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, telefono TEXT,
            email TEXT, direccion TEXT, documento TEXT, creado_en DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS facturas_cobrar (
            id INTEGER PRIMARY KEY AUTOINCREMENT, venta_id INTEGER NOT NULL,
            cliente_id INTEGER, cliente_nombre TEXT DEFAULT 'Consumidor Final',
            tipo_venta TEXT DEFAULT 'contado', total_usd REAL NOT NULL, total_ves REAL NOT NULL,
            saldo_usd REAL NOT NULL DEFAULT 0, saldo_ves REAL NOT NULL DEFAULT 0,
            estado TEXT DEFAULT 'pagado', fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP,
            nro_factura TEXT,
            FOREIGN KEY(venta_id) REFERENCES ventas(id) ON DELETE CASCADE)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS abonos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, factura_id INTEGER NOT NULL,
            monto_usd REAL NOT NULL, monto_ves REAL NOT NULL, tasa_usada REAL,
            metodo_pago TEXT DEFAULT 'efectivo', notas TEXT,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(factura_id) REFERENCES facturas_cobrar(id) ON DELETE CASCADE)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS config_tasa (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tasa_oficial REAL DEFAULT 36.00,
            tasa_manual1 REAL DEFAULT 38.00, tasa_manual2 REAL DEFAULT 40.00,
            tasa_activa TEXT DEFAULT 'oficial', fuente_oficial TEXT DEFAULT 'BCV',
            actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP, actualizado_por INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS historial_tasas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tasa_oficial REAL, tasa_manual1 REAL,
            tasa_manual2 REAL, tasa_activa TEXT, cambio_por TEXT,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('INSERT OR IGNORE INTO config_tasa (id,tasa_oficial,tasa_manual1,tasa_manual2,tasa_activa,fuente_oficial) VALUES (1,36.00,38.00,40.00,"oficial","BCV")')
        conn.commit()
    except Exception as e: logging.error(f"Error BD usuario: {e}")
    finally:
        if conn: conn.close()

def ensure_user_db_tables(user_id):
    db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
    if not os.path.exists(db_path): init_user_db(user_id); return
    conn = None
    try:
        conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
        cursor = conn.cursor(); cursor.execute("PRAGMA table_info(productos)")
        cols = [c[1] for c in cursor.fetchall()]
        for col, tipo in [('imagen_principal','TEXT'),('medida_unidad','TEXT DEFAULT "Unidad"'),('medida_cantidad','REAL DEFAULT 1')]:
            if col not in cols:
                try: conn.execute(f'ALTER TABLE productos ADD COLUMN {col} {tipo}')
                except: pass
        for col, tipo in [('tipo_venta','TEXT DEFAULT "contado"'),('cliente_id','INTEGER'),('cliente_nombre','TEXT DEFAULT "Consumidor Final"')]:
            try: conn.execute(f'ALTER TABLE ventas ADD COLUMN {col} {tipo}')
            except: pass
        for tabla, sql in [
            ('clientes','CREATE TABLE IF NOT EXISTS clientes (id INTEGER PRIMARY KEY AUTOINCREMENT, nombre TEXT NOT NULL, telefono TEXT, email TEXT, direccion TEXT, documento TEXT, creado_en DATETIME DEFAULT CURRENT_TIMESTAMP)'),
            ('facturas_cobrar','CREATE TABLE IF NOT EXISTS facturas_cobrar (id INTEGER PRIMARY KEY AUTOINCREMENT, venta_id INTEGER NOT NULL, cliente_id INTEGER, cliente_nombre TEXT DEFAULT "Consumidor Final", tipo_venta TEXT DEFAULT "contado", total_usd REAL NOT NULL, total_ves REAL NOT NULL, saldo_usd REAL NOT NULL DEFAULT 0, saldo_ves REAL NOT NULL DEFAULT 0, estado TEXT DEFAULT "pagado", fecha_creacion DATETIME DEFAULT CURRENT_TIMESTAMP, nro_factura TEXT, FOREIGN KEY(venta_id) REFERENCES ventas(id) ON DELETE CASCADE)'),
            ('abonos','CREATE TABLE IF NOT EXISTS abonos (id INTEGER PRIMARY KEY AUTOINCREMENT, factura_id INTEGER NOT NULL, monto_usd REAL NOT NULL, monto_ves REAL NOT NULL, tasa_usada REAL, metodo_pago TEXT DEFAULT "efectivo", notas TEXT, creado_en DATETIME DEFAULT CURRENT_TIMESTAMP, FOREIGN KEY(factura_id) REFERENCES facturas_cobrar(id) ON DELETE CASCADE)')]:
            try: conn.execute(f"SELECT 1 FROM {tabla} LIMIT 1")
            except:
                try: conn.execute(sql)
                except: pass
        conn.commit()
    except Exception as e: logging.error(f"Error asegurando tablas: {e}")
    finally:
        if conn: conn.close()

# ============ FUNCIONES DE SUSCRIPCIONES ============
def obtener_suscripcion_activa(uid):
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        s = conn.execute("SELECT s.* FROM suscripciones s WHERE s.usuario_id=? AND s.estado='activa' AND s.fecha_fin>=date('now') ORDER BY s.fecha_fin DESC LIMIT 1",(uid,)).fetchone()
        conn.close(); return dict(s) if s else None
    except: return None

def verificar_limites_usuario(uid):
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        u = conn.execute('SELECT suscripcion_activa,fecha_fin_suscripcion,limite_productos,limite_ventas_mensuales FROM usuarios WHERE id=?',(uid,)).fetchone()
        conn.close()
        if not u: return {'suscripcion_activa':False,'limite_productos':50,'limite_ventas':20,'plan':'Free'}
        activa = u['suscripcion_activa'] and u['fecha_fin_suscripcion'] and u['fecha_fin_suscripcion']>=str(date.today())
        return {'suscripcion_activa':activa,'limite_productos':u['limite_productos']or 50,'limite_ventas':u['limite_ventas_mensuales']or 20,'plan':'VIP' if activa else 'Free'}
    except: return {'suscripcion_activa':False,'limite_productos':50,'limite_ventas':20,'plan':'Free'}

def cantidad_productos_usuario(uid):
    try:
        p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        if not os.path.exists(p): return 0
        return sqlite3.connect(p,timeout=20).execute('SELECT COUNT(*) as c FROM productos').fetchone()['c']
    except: return 0

def ventas_mensuales_usuario(uid):
    try:
        p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        if not os.path.exists(p): return 0
        return sqlite3.connect(p,timeout=20).execute("SELECT COUNT(*) as c FROM ventas WHERE strftime('%Y-%m',creado_en)=strftime('%Y-%m','now')").fetchone()['c']
    except: return 0

def obtener_limite_productos_por_plan(plan): return 999999 if plan.lower()=='vip' else 50
def obtener_limite_ventas_por_plan(plan): return 999999 if plan.lower()=='vip' else 20

# ============ CONFIGURACION DE EMPRESA ============
def obtener_config_empresa(uid=None):
    if uid is None: uid = session.get('user_id')
    if uid is None: return {'nombre':'Mi Empresa','rif':'J-00000000-0','direccion':'Venezuela','telefono':'','email':'','mensaje_factura':'Gracias por su compra!'}
    try:
        c = sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); c.row_factory = sqlite3.Row
        r = c.execute('SELECT * FROM config_empresa WHERE usuario_id=?',(uid,)).fetchone(); c.close()
        return dict(r) if r else {'nombre':'Mi Empresa','rif':'J-00000000-0','direccion':'Venezuela','telefono':'','email':'','mensaje_factura':'Gracias por su compra!'}
    except: return {'nombre':'Mi Empresa','rif':'J-00000000-0','direccion':'Venezuela','telefono':'','email':'','mensaje_factura':'Gracias por su compra!'}

def actualizar_config_empresa(uid,datos):
    try:
        c = sqlite3.connect(DATABASE_PRINCIPAL,timeout=20)
        if c.execute('SELECT id FROM config_empresa WHERE usuario_id=?',(uid,)).fetchone():
            c.execute('UPDATE config_empresa SET nombre=?,rif=?,direccion=?,telefono=?,email=?,mensaje_factura=?,actualizado_en=CURRENT_TIMESTAMP WHERE usuario_id=?',
                (datos.get('nombre',''),datos.get('rif',''),datos.get('direccion',''),datos.get('telefono',''),datos.get('email',''),datos.get('mensaje_factura',''),uid))
        else:
            c.execute('INSERT INTO config_empresa (usuario_id,nombre,rif,direccion,telefono,email,mensaje_factura) VALUES (?,?,?,?,?,?,?)',
                (uid,datos.get('nombre',''),datos.get('rif',''),datos.get('direccion',''),datos.get('telefono',''),datos.get('email',''),datos.get('mensaje_factura','')))
        c.commit(); c.close(); return True
    except: return False

# ============ FUNCIONES DE CLIENTES Y FACTURAS ============
def obtener_clientes(uid):
    try:
        p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        if not os.path.exists(p): return []
        c = sqlite3.connect(p,timeout=20); c.row_factory = sqlite3.Row
        r = c.execute('SELECT * FROM clientes ORDER BY nombre').fetchall(); c.close()
        return [dict(x) for x in r]
    except: return []

def agregar_cliente(uid,datos):
    try:
        p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        c = sqlite3.connect(p,timeout=20)
        cur = c.execute('INSERT INTO clientes (nombre,telefono,email,direccion,documento) VALUES (?,?,?,?,?)',
            (datos['nombre'],datos.get('telefono',''),datos.get('email',''),datos.get('direccion',''),datos.get('documento','')))
        cid = cur.lastrowid; c.commit(); c.close(); return cid
    except: return None

def obtener_facturas_cobrar(uid, filtro='todas', cliente_id=None):
    try:
        p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        if not os.path.exists(p): return []
        conn = sqlite3.connect(p, timeout=30); conn.row_factory = sqlite3.Row
        q = '''SELECT fc.*, v.creado_en as fv, cl.documento as cliente_doc, cl.telefono as cliente_tel
               FROM facturas_cobrar fc LEFT JOIN ventas v ON fc.venta_id=v.id LEFT JOIN clientes cl ON fc.cliente_id=cl.id WHERE 1=1'''
        params = []
        if filtro == 'contado': q += " AND fc.tipo_venta='contado'"
        elif filtro == 'credito': q += " AND fc.tipo_venta='credito'"
        elif filtro == 'pendiente': q += " AND fc.estado='pendiente'"
        elif filtro == 'pagado': q += " AND fc.estado='pagado'"
        if cliente_id: q += " AND fc.cliente_id=?"; params.append(cliente_id)
        q += ' ORDER BY fc.id DESC LIMIT 200'
        facturas = conn.execute(q, params).fetchall()
        res = []
        for f in facturas:
            fd = dict(f)
            abonos = conn.execute('SELECT * FROM abonos WHERE factura_id=? ORDER BY creado_en DESC', (fd['id'],)).fetchall()
            fd['abonos'] = [dict(a) for a in abonos]
            fd['total_abonado_usd'] = sum(a['monto_usd'] for a in abonos)
            res.append(fd)
        conn.close(); return res
    except Exception as e:
        logging.error(f"Error obtener_facturas: {e}")
        return []

def agregar_abono(uid, fid, monto_usd, monto_ves, tasa_usada, metodo_pago='efectivo', notas=''):
    try:
        p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        conn = sqlite3.connect(p, timeout=30)
        conn.execute('PRAGMA busy_timeout = 5000')
        conn.row_factory = sqlite3.Row
        
        conn.execute('INSERT INTO abonos (factura_id,monto_usd,monto_ves,tasa_usada,metodo_pago,notas) VALUES (?,?,?,?,?,?)',
            (fid, round(monto_usd,2), round(monto_ves,2), round(tasa_usada,2), metodo_pago, notas))
        
        fc = conn.execute('SELECT total_usd, saldo_usd, saldo_ves FROM facturas_cobrar WHERE id=?', (fid,)).fetchone()
        if fc:
            saldo_actual = round(fc['saldo_usd'], 2)
            monto = round(monto_usd, 2)
            nuevo_saldo_usd = round(max(0, saldo_actual - monto), 2)
            nuevo_saldo_ves = round(max(0, round(fc['saldo_ves'], 2) - round(monto_ves, 2)), 2)
            nuevo_estado = 'pagado' if nuevo_saldo_usd < 0.01 else 'pendiente'
            
            conn.execute('UPDATE facturas_cobrar SET saldo_usd=?, saldo_ves=?, estado=? WHERE id=?',
                (nuevo_saldo_usd, nuevo_saldo_ves, nuevo_estado, fid))
            conn.commit()
            logging.info(f"[ABONO] Factura#{fid}: Monto={monto}, Saldo={nuevo_saldo_usd}, Estado={nuevo_estado}")
        conn.close()
        return True
    except Exception as e:
        logging.error(f"Error abono: {e}")
        return False

# ============ FUNCION DE FACTURA HTML ============
def generar_factura_venta(venta_id, productos_vendidos, tasas, uid, vendedor_nombre, metodo_pago, observaciones, tipo_venta='contado', cliente_nombre='Consumidor Final', cliente_id=None):
    config = obtener_config_empresa(uid)
    fecha = datetime.now(); fs = fecha.strftime('%d/%m/%Y %H:%M:%S')
    nf = f"F-{fecha.strftime('%Y%m%d%H%M%S')}-{venta_id}"
    total_usd = sum(it['cantidad']*it['precio_usd'] for it in productos_vendidos)
    tav = tasas.get(tasas.get('activa','oficial'),0); tv = total_usd*tav
    tipo_label = 'CONTADO' if tipo_venta=='contado' else 'CREDITO'
    cliente_doc = ''
    if cliente_id:
        try:
            p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
            if os.path.exists(p):
                c = sqlite3.connect(p, timeout=5); c.row_factory = sqlite3.Row
                cl = c.execute('SELECT documento FROM clientes WHERE id=?', (cliente_id,)).fetchone(); c.close()
                if cl and cl['documento']: cliente_doc = cl['documento']
        except: pass
    cliente_linea = f"{cliente_nombre}{' - CI/RIF: '+cliente_doc if cliente_doc else ''}"
    html = f'''<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>Factura {nf}</title>
<style>@page{{size:A4;margin:1cm}}body{{font-family:Arial;font-size:12px;margin:0;padding:20px;color:#333}}
.header{{text-align:center;border-bottom:2px solid #1e3a5f;padding-bottom:15px;margin-bottom:15px}}
.header h1{{margin:0;font-size:22px;color:#1e3a5f;text-transform:uppercase}}
.header .rif{{font-size:12px;color:#666}}.header .dir{{font-size:11px;color:#666}}.header .contacto{{font-size:10px;color:#888}}
.info{{display:flex;justify-content:space-between;margin-bottom:15px;font-size:11px}}
.info strong{{color:#1e3a5f}}
.tipo-badge{{display:inline-block;padding:3px 10px;border-radius:4px;font-weight:bold;font-size:10px;text-transform:uppercase;margin-bottom:8px}}
.tipo-contado{{background:#d1fae5;color:#065f46}}.tipo-credito{{background:#fef3c7;color:#92400e}}
table.items{{width:100%;border-collapse:collapse;margin:15px 0;font-size:11px}}
table.items thead th{{background:#1e3a5f;color:white;padding:8px 10px;text-align:left;font-weight:600;font-size:10px;text-transform:uppercase}}
table.items tbody td{{padding:8px 10px;border-bottom:1px solid #e2e8f0}}
.tr{{text-align:right}}.tc{{text-align:center}}
.totales{{width:100%;max-width:300px;margin-left:auto;margin-top:10px}}
.totales table{{width:100%;font-size:12px}}.totales td{{padding:5px 10px}}
.totales .tf{{font-size:16px;font-weight:bold;color:#1e3a5f;border-top:2px solid #1e3a5f}}
.tasa-info{{background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:8px 12px;margin:10px 0;font-size:10px;text-align:center}}
.footer{{text-align:center;border-top:2px solid #1e3a5f;padding-top:10px;margin-top:20px;font-size:10px;color:#666}}
@media print{{body{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}.no-print{{display:none}}}}</style></head><body>
<div class="header"><h1>{config.get('nombre','Mi Empresa')}</h1><div class="rif">RIF: {config.get('rif','J-00000000-0')}</div><div class="dir">{config.get('direccion','Venezuela')}</div><div class="contacto">Tel: {config.get('telefono','N/A')} | Email: {config.get('email','N/A')}</div></div>
<div class="tipo-badge tipo-{'contado' if tipo_venta=='contado' else 'credito'}">{tipo_label}</div>
<div class="info"><div><strong>FACTURA:</strong> {nf}<br><strong>Fecha:</strong> {fs}<br><strong>Vendedor:</strong> {vendedor_nombre}</div><div><strong>Cliente:</strong> {cliente_linea}<br><strong>Pago:</strong> {metodo_pago.upper()}<br><strong>Tasa:</strong> {tasas.get('activa','Oficial').upper()}</div></div>
<table class="items"><thead><tr><th class="tc">Cant</th><th>Producto</th><th class="tr">P.Unit(USD)</th><th class="tr">Total(USD)</th><th class="tr">Total(VES)</th></tr></thead><tbody>'''
    for it in productos_vendidos:
        sub = it['cantidad']*it['precio_usd']; sv = sub*tav
        html += f'<tr><td class="tc">{it["cantidad"]}</td><td>{it["nombre"]}</td><td class="tr">${it["precio_usd"]:.2f}</td><td class="tr">${sub:.2f}</td><td class="tr">Bs.{sv:.2f}</td></tr>'
    html += f'''</tbody></table>
<div class="totales"><table><tr><td><strong>Total USD:</strong></td><td class="tr">${total_usd:.2f}</td></tr><tr><td><strong>Total VES:</strong></td><td class="tr tf">Bs.{tv:.2f}</td></tr></table></div>
<div class="tasa-info"><strong>Tasa:</strong> {tasas.get('activa','Oficial').upper()} | <strong>Valor:</strong> Bs.{tav:.2f}/USD</div>
{f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px 15px;margin:10px 0;font-size:11px"><strong>Obs:</strong> {observaciones}</div>' if observaciones else ''}
<div class="footer"><p>Generado el {fs}</p></div></body></html>'''
    fd = os.path.join('facturas')
    if not os.path.exists(fd): os.makedirs(fd)
    fn = f"factura_{nf.replace('/','-')}_{uid}.html"
    fp = os.path.join(fd, fn)
    with open(fp, 'w', encoding='utf-8') as f: f.write(html)
    return fp, fn, nf

# ============ FUNCIONES DE TASAS ============
def obtener_tasas_usuario(uid):
    d = {'oficial':36,'manual1':38,'manual2':40,'activa':'oficial','fuente_oficial':'BCV','actualizado_en':''}
    try:
        init_user_db(uid)
        p = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        c = sqlite3.connect(p,timeout=20); c.row_factory = sqlite3.Row
        r = c.execute('SELECT * FROM config_tasa ORDER BY id DESC LIMIT 1').fetchone(); c.close()
        if r:
            rd = dict(r)
            return {'oficial':float(rd.get('tasa_oficial',36)or 36),'manual1':float(rd.get('tasa_manual1',38)or 38),'manual2':float(rd.get('tasa_manual2',40)or 40),'activa':rd.get('tasa_activa')or'oficial','fuente_oficial':rd.get('fuente_oficial')or'BCV','actualizado_en':rd.get('actualizado_en')or''}
    except: pass
    return d

def calcular_precios_todas_tasas(pu,tasas):
    if pu is None: pu=0
    pu=float(pu)
    return {'usd':round(pu,2),'oficial':round(pu*float(tasas.get('oficial',36)),2),'manual1':round(pu*float(tasas.get('manual1',38)),2),'manual2':round(pu*float(tasas.get('manual2',40)),2)}

def obtener_tasa_bcv():
    cf = 'tasa_cache.json'
    if os.path.exists(cf):
        try:
            with open(cf,'r',encoding='utf-8') as f:
                ca = json.load(f)
                if (datetime.now()-datetime.fromtimestamp(ca.get('timestamp',0))).seconds < 3600: return float(ca['tasa'])
        except: pass
    try:
        from bs4 import BeautifulSoup
        r = requests.get('http://www.bcv.org.ve/',headers={'User-Agent':'Mozilla/5.0'},timeout=15,verify=False)
        if r.status_code==200:
            soup = BeautifulSoup(r.text,'html.parser')
            d = soup.find('div',{'id':'dolar'})
            if d:
                s = d.find('strong',class_='strong-tb')
                if s:
                    t = float(s.text.strip().replace('.','').replace(',','.'))
                    if 10<t<1000:
                        with open(cf,'w',encoding='utf-8') as f: json.dump({'tasa':t,'fuente':'BCV','timestamp':datetime.now().timestamp()},f)
                        return t
    except: pass
    try:
        r = requests.get('https://api.exchangerate-api.com/v4/latest/USD',timeout=10)
        if r.status_code==200:
            t = float(r.json().get('rates',{}).get('VES',0))
            if 10<t<1000:
                with open(cf,'w',encoding='utf-8') as f: json.dump({'tasa':t,'fuente':'ExchangeRate','timestamp':datetime.now().timestamp()},f)
                return t
    except: pass
    if os.path.exists(cf):
        try:
            with open(cf,'r',encoding='utf-8') as f: return float(json.load(f).get('tasa',36))
        except: pass
    return 36.00

# ============ DECORADORES ============
def login_required(f):
    @wraps(f)
    def dec(*a,**k):
        if 'user_id' not in session: flash('Inicia sesion','warning'); return redirect(url_for('login'))
        return f(*a,**k)
    return dec

def admin_required(f):
    @wraps(f)
    def dec(*a,**k):
        if session.get('role')!='admin': flash('Acceso denegado','danger'); return redirect(url_for('index'))
        return f(*a,**k)
    return dec

def vendedor_required(f):
    @wraps(f)
    def dec(*a,**k):
        if not session.get('es_vendedor'): flash('Acceso denegado','danger'); return redirect(url_for('index'))
        return f(*a,**k)
    return dec

@app.context_processor
def inject_globals():
    return {'now':datetime.now(),'app_name':'Sistema de Gestion','user_id':session.get('user_id'),'user_name':session.get('user_name'),'user_role':session.get('role'),'verificar_limites_usuario':verificar_limites_usuario,'obtener_tasa_bcv':obtener_tasa_bcv}

# ============ RUTAS DE AUTENTICACION ============
@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role')=='admin': return redirect(url_for('admin_dashboard'))
        elif session.get('es_vendedor'): return redirect(url_for('vendedor_ventas'))
        return redirect(url_for('user_dashboard'))
    return redirect(url_for('login'))

@app.route('/login',methods=['GET','POST'])
def login():
    if request.method=='POST':
        email=request.form.get('email','').strip(); password=request.form.get('password','')
        if not email or not password: flash('Email y contrasena requeridos','danger'); return render_template('login.html')
        conn=None
        try:
            conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
            user=conn.execute('SELECT * FROM usuarios WHERE email=?',(email,)).fetchone()
            if not user: flash('Credenciales invalidas','danger'); conn.close(); return render_template('login.html')
            ud=dict(user)
            if not check_password_hash(ud['password_hash'],password): flash('Credenciales invalidas','danger'); conn.close(); return render_template('login.html')
            if not ud['activo']: flash('Usuario inactivo','danger'); conn.close(); return render_template('login.html')
            session['user_id']=ud['id']; session['user_name']=ud['nombre']; session['role']=ud['role']
            session['es_vendedor']=bool(ud['es_vendedor']); session['usuario_padre_id']=ud['usuario_padre_id']
            session['tasa_activa']=ud.get('tasa_activa')or'oficial'
            conn.close()
            if not session.get('es_vendedor'):
                try: init_user_db(session['user_id'])
                except: pass
            flash(f'Bienvenido {ud["nombre"]}!','success')
            if ud['role']=='admin': return redirect(url_for('admin_dashboard'))
            elif ud['es_vendedor']: return redirect(url_for('vendedor_ventas'))
            return redirect(url_for('user_dashboard'))
        except Exception as e: logging.error(f"Error login: {e}"); flash('Error del servidor','danger')
        finally:
            if conn: conn.close()
    return render_template('login.html')

@app.route('/register',methods=['GET','POST'])
def register():
    if request.method=='POST':
        nombre=request.form.get('nombre','').strip(); email=request.form.get('email','').strip()
        password=request.form.get('password',''); telefono=request.form.get('telefono','').strip()
        if not nombre or not email or not password: flash('Todos los campos obligatorios','danger'); return render_template('register.html')
        if len(password)<6: flash('Minimo 6 caracteres','danger'); return render_template('register.html')
        conn=None
        try:
            conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
            if conn.execute('SELECT id FROM usuarios WHERE email=?',(email,)).fetchone(): flash('Email ya registrado','danger'); conn.close(); return render_template('register.html')
            ph=generate_password_hash(password)
            cur=conn.execute('INSERT INTO usuarios (nombre,email,password_hash,telefono) VALUES (?,?,?,?)',(nombre,email,ph,telefono))
            uid=cur.lastrowid; conn.commit(); conn.close()
            init_user_db(uid)
            flash('Registro exitoso','success'); return redirect(url_for('login'))
        except Exception as e: logging.error(f"Error registro: {e}"); flash('Error del servidor','danger')
        finally:
            if conn: conn.close()
    return render_template('register.html')

@app.route('/logout')
def logout(): session.clear(); flash('Sesion cerrada','info'); return redirect(url_for('login'))

# ============ RUTAS DE USUARIO ============
@app.route('/user/dashboard')
@login_required
def user_dashboard():
    uid=session['user_id']; init_user_db(uid)
    limites={'suscripcion_activa':False,'limite_productos':50,'limite_ventas':20,'plan':'Free'}
    suscripcion=None; tasa_bcv=36.00
    tasas={'oficial':36,'manual1':38,'manual2':40,'activa':'oficial'}
    stats={'total_productos':0,'productos_bajo_stock':0,'ventas_mes':0,'ingresos_mes_usd':0}
    alertas_stock=[]; ventas_por_dia=[]; ventas_categoria=[]; meses_anteriores=[]
    try:
        limites=verificar_limites_usuario(uid); suscripcion=obtener_suscripcion_activa(uid)
        tasa_bcv=obtener_tasa_bcv(); tasas=obtener_tasas_usuario(uid)
        dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20); conn.row_factory=sqlite3.Row
        stats=conn.execute('''SELECT 
            COALESCE((SELECT COUNT(*) FROM productos),0) as tp,
            COALESCE((SELECT COUNT(*) FROM productos WHERE stock_actual<=stock_minimo AND stock_actual>=0),0) as pbs,
            COALESCE((SELECT COUNT(*) FROM ventas WHERE strftime("%Y-%m",creado_en)=strftime("%Y-%m","now")),0) as vm,
            COALESCE((SELECT SUM(total_usd) FROM ventas WHERE strftime("%Y-%m",creado_en)=strftime("%Y-%m","now")),0) as imu''').fetchone()
        alertas_stock=conn.execute('SELECT nombre,codigo,stock_actual,stock_minimo FROM productos WHERE stock_actual<=stock_minimo AND stock_actual>=0 ORDER BY stock_actual ASC LIMIT 5').fetchall()
        ventas_por_dia=conn.execute("SELECT date(creado_en) as dia, COUNT(*) as cantidad, SUM(total_usd) as total FROM ventas WHERE strftime('%Y-%m',creado_en)=strftime('%Y-%m','now') GROUP BY dia ORDER BY dia").fetchall()
        ventas_categoria=conn.execute('SELECT p.categoria, COUNT(*) as cantidad, SUM(v.total_usd) as total FROM ventas v JOIN productos p ON v.producto_id=p.id GROUP BY p.categoria ORDER BY cantidad DESC').fetchall()
        meses_anteriores=conn.execute("SELECT strftime('%Y-%m',creado_en) as mes, COUNT(*) as cantidad, SUM(total_usd) as total_usd FROM ventas WHERE strftime('%Y-%m',creado_en)<strftime('%Y-%m','now') GROUP BY mes ORDER BY mes DESC LIMIT 6").fetchall()
        conn.close()
    except Exception as e: logging.error(f"Error dashboard: {e}"); flash('Error cargando dashboard','warning')
    return render_template('user/dashboard.html',suscripcion=suscripcion,
        stats={'total_productos':stats['tp']or 0,'productos_bajo_stock':stats['pbs']or 0,'ventas_mes':stats['vm']or 0,'ingresos_mes_usd':stats['imu']or 0},
        alertas_stock=alertas_stock,ventas_por_dia=ventas_por_dia,ventas_categoria=ventas_categoria,meses_anteriores=meses_anteriores,tasas=tasas,limites=limites,tasa_bcv=tasa_bcv)

@app.route('/user/inventario')
@login_required
def user_inventario():
    uid=session['user_id']; search=request.args.get('search','').strip()
    categoria=request.args.get('categoria','').strip(); subcategoria=request.args.get('subcategoria','').strip()
    marca=request.args.get('marca','').strip(); stock_bajo=request.args.get('stock_bajo','')
    productos=[]; categorias_db=[]; subcategorias_db=[]
    tasas=obtener_tasas_usuario(uid); stats={'total':0,'stock_total':0,'valor_total':0,'stock_bajo':0}
    try:
        init_user_db(uid); dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20); conn.row_factory=sqlite3.Row
        q='SELECT * FROM productos WHERE 1=1'; params=[]
        if search: q+=' AND (nombre LIKE ? OR codigo LIKE ? OR referencia LIKE ?)'; params.extend([f'%{search}%']*3)
        if categoria: q+=' AND categoria=?'; params.append(categoria)
        if subcategoria: q+=' AND subcategoria=?'; params.append(subcategoria)
        if marca: q+=' AND marca=?'; params.append(marca)
        if stock_bajo=='1': q+=' AND stock_actual<=stock_minimo AND stock_actual>=0'
        q+=' ORDER BY nombre'
        pr=conn.execute(q,params).fetchall()
        categorias_db=conn.execute("SELECT DISTINCT categoria FROM productos WHERE categoria IS NOT NULL AND categoria!='' ORDER BY categoria").fetchall()
        if categoria: subcategorias_db=conn.execute("SELECT DISTINCT subcategoria FROM productos WHERE categoria=? AND subcategoria IS NOT NULL AND subcategoria!='' ORDER BY subcategoria",(categoria,)).fetchall()
        sr=conn.execute('''SELECT COALESCE(COUNT(*),0) as total, COALESCE(SUM(stock_actual),0) as stock_total, 
            COALESCE(SUM(precio_venta_usd*stock_actual),0) as valor_total,
            COALESCE((SELECT COUNT(*) FROM productos WHERE stock_actual<=stock_minimo AND stock_actual>=0),0) as stock_bajo FROM productos''').fetchone()
        if sr: stats={'total':sr['total']or 0,'stock_total':sr['stock_total']or 0,'valor_total':sr['valor_total']or 0,'stock_bajo':sr['stock_bajo']or 0}
        conn.close()
        for prod in pr:
            pd=dict(prod); precios=calcular_precios_todas_tasas(pd.get('precio_venta_usd',0)or 0,tasas)
            iname=pd.get('imagen_principal') or obtener_imagen_producto(uid,pd['id'])
            iurl=f"/uploads/{uid}/{iname}" if iname else None
            productos.append({**pd,'precios':precios,'imagen_url':iurl})
    except Exception as e: logging.error(f"Error inventario: {e}"); flash('Error','warning')
    return render_template('user/inventario.html',productos=productos,categorias_db=categorias_db,categorias_predefinidas=obtener_categorias_predefinidas(),subcategorias_db=subcategorias_db,subcategorias_moto=obtener_subcategorias_por_categoria('Repuestos Moto'),marcas_moto=obtener_marcas_moto(),search=search,categoria=categoria,subcategoria=subcategoria,marca=marca,stock_bajo=stock_bajo,tasas=tasas,stats=stats)

@app.route('/user/agregar_producto',methods=['POST'])
@login_required
def agregar_producto():
    uid=session['user_id']; limites=verificar_limites_usuario(uid); ca=cantidad_productos_usuario(uid)
    if ca>=limites['limite_productos']: flash(f'Limite de {limites["limite_productos"]} productos. Adquiere VIP.','warning'); return redirect(url_for('user_inventario'))
    try:
        nombre=request.form.get('nombre','').strip(); codigo=request.form.get('codigo','').strip()
        categoria=request.form.get('categoria','').strip()
        if not nombre or not codigo or not categoria: flash('Nombre,codigo y categoria obligatorios','danger'); return redirect(url_for('user_inventario'))
        pc=float(request.form.get('precio_costo_usd',0)); pv=float(request.form.get('precio_venta_usd',0))
        sa=int(request.form.get('stock_actual',0)); sm=int(request.form.get('stock_minimo',5))
        if pv<=0: flash('Precio de venta mayor a 0','danger'); return redirect(url_for('user_inventario'))
        mg=((pv-pc)/pc*100) if pc>0 else 0
        init_user_db(uid); dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20); conn.row_factory=sqlite3.Row
        if conn.execute('SELECT id FROM productos WHERE codigo=?',(codigo,)).fetchone(): flash('Codigo ya existe','danger'); conn.close(); return redirect(url_for('user_inventario'))
        cur=conn.execute('''INSERT INTO productos (nombre,codigo,categoria,subcategoria,marca,modelo,referencia,color,descripcion,precio_costo_usd,precio_venta_usd,margen_ganancia,stock_actual,stock_minimo,ubicacion,fecha_expiracion,impuesto,proveedor_nombre,proveedor_contacto,proveedor_notas,pagado_contado,dias_credito,observaciones,medida_unidad,medida_cantidad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (nombre,codigo,categoria,request.form.get('subcategoria','').strip(),request.form.get('marca','').strip(),request.form.get('modelo','').strip(),request.form.get('referencia','').strip(),request.form.get('color','').strip(),request.form.get('descripcion','').strip(),pc,pv,mg,sa,sm,request.form.get('ubicacion','').strip(),request.form.get('fecha_expiracion')or None,float(request.form.get('impuesto',16)),request.form.get('proveedor_nombre','').strip(),request.form.get('proveedor_contacto','').strip(),request.form.get('proveedor_notas','').strip(),request.form.get('pagado_contado')=='1',int(request.form.get('dias_credito',0)),request.form.get('observaciones','').strip(),request.form.get('medida_unidad','Unidad').strip(),float(request.form.get('medida_cantidad',1))))
        pid=cur.lastrowid
        if 'imagen_producto' in request.files:
            img=request.files['imagen_producto']
            if img and img.filename:
                iname=guardar_imagen_producto(img,uid,pid)
                if iname: conn.execute('UPDATE productos SET imagen_principal=? WHERE id=?',(iname,pid))
        conn.commit(); conn.close()
        flash('Producto agregado','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/editar_producto/<int:producto_id>',methods=['POST'])
@login_required
def editar_producto(producto_id):
    uid=session['user_id']
    try:
        nombre=request.form.get('nombre','').strip(); categoria=request.form.get('categoria','').strip()
        if not nombre or not categoria: flash('Nombre y categoria obligatorios','danger'); return redirect(url_for('user_inventario'))
        pc=float(request.form.get('precio_costo_usd',0)); pv=float(request.form.get('precio_venta_usd',0))
        sa=int(request.form.get('stock_actual',0)); sm=int(request.form.get('stock_minimo',5))
        if pv<=0: flash('Precio mayor a 0','danger'); return redirect(url_for('user_inventario'))
        mg=((pv-pc)/pc*100) if pc>0 else 0
        init_user_db(uid); dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20)
        if 'imagen_producto' in request.files:
            img=request.files['imagen_producto']
            if img and img.filename:
                eliminar_imagenes_producto(uid,producto_id)
                iname=guardar_imagen_producto(img,uid,producto_id)
                if iname: conn.execute('UPDATE productos SET imagen_principal=? WHERE id=?',(iname,producto_id))
        cur=conn.execute('''UPDATE productos SET nombre=?,categoria=?,subcategoria=?,marca=?,modelo=?,referencia=?,color=?,descripcion=?,precio_costo_usd=?,precio_venta_usd=?,margen_ganancia=?,stock_actual=?,stock_minimo=?,ubicacion=?,fecha_expiracion=?,impuesto=?,observaciones=?,medida_unidad=?,medida_cantidad=?,actualizado_en=CURRENT_TIMESTAMP WHERE id=?''',
            (nombre,categoria,request.form.get('subcategoria','').strip(),request.form.get('marca','').strip(),request.form.get('modelo','').strip(),request.form.get('referencia','').strip(),request.form.get('color','').strip(),request.form.get('descripcion','').strip(),pc,pv,mg,sa,sm,request.form.get('ubicacion','').strip(),request.form.get('fecha_expiracion')or None,float(request.form.get('impuesto',16)),request.form.get('observaciones','').strip(),request.form.get('medida_unidad','Unidad').strip(),float(request.form.get('medida_cantidad',1)),producto_id))
        if cur.rowcount>0: conn.commit(); flash('Producto actualizado','success')
        else: flash('Producto no encontrado','danger')
        conn.close()
    except: flash('Error','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/eliminar_producto/<int:producto_id>',methods=['POST'])
@login_required
def eliminar_producto(producto_id):
    uid=session['user_id']
    try:
        init_user_db(uid); dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20)
        if conn.execute('SELECT COUNT(*) as c FROM ventas WHERE producto_id=?',(producto_id,)).fetchone()['c']>0:
            flash('Tiene ventas asociadas','danger'); conn.close(); return redirect(url_for('user_inventario'))
        conn.execute('DELETE FROM productos WHERE id=?',(producto_id,)); conn.commit(); conn.close()
        eliminar_imagenes_producto(uid,producto_id)
        flash('Producto eliminado','success')
    except: flash('Error','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/ajustar_stock/<int:producto_id>',methods=['POST'])
@login_required
def ajustar_stock(producto_id):
    uid=session['user_id']
    try:
        ta=request.form.get('tipo_ajuste','').strip(); cant=int(request.form.get('cantidad',0))
        if cant<=0 or ta not in['entrada','salida']: flash('Datos invalidos','danger'); return redirect(url_for('user_inventario'))
        init_user_db(uid); dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20); conn.row_factory=sqlite3.Row
        p=conn.execute('SELECT nombre,stock_actual FROM productos WHERE id=?',(producto_id,)).fetchone()
        if not p: flash('No encontrado','danger'); conn.close(); return redirect(url_for('user_inventario'))
        ns=p['stock_actual']+cant if ta=='entrada' else p['stock_actual']-cant
        if ns<0: flash(f'Stock insuficiente. Disponible: {p["stock_actual"]}','danger'); conn.close(); return redirect(url_for('user_inventario'))
        conn.execute('UPDATE productos SET stock_actual=?, actualizado_en=CURRENT_TIMESTAMP WHERE id=?',(ns,producto_id))
        conn.commit(); conn.close()
        flash(f'Stock ajustado. Nuevo: {ns}','success')
    except: flash('Error','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/ventas')
@login_required
def user_ventas():
    uid=session['user_id']; init_user_db(uid)
    try:
        dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20); conn.row_factory=sqlite3.Row
        pr=conn.execute('SELECT id,nombre,codigo,precio_venta_usd,stock_actual,categoria,marca,imagen_principal,medida_unidad,medida_cantidad FROM productos WHERE stock_actual>0 ORDER BY nombre').fetchall()
        vr=conn.execute('SELECT v.*, p.nombre as pn FROM ventas v JOIN productos p ON v.producto_id=p.id ORDER BY v.creado_en DESC LIMIT 10').fetchall()
        stats=conn.execute('SELECT COUNT(*) as tv, COALESCE(SUM(total_usd),0) as iu FROM ventas').fetchone()
        sm=conn.execute("SELECT COUNT(*) as vm, COALESCE(SUM(total_usd),0) as imu FROM ventas WHERE strftime('%Y-%m',creado_en)=strftime('%Y-%m','now')").fetchone()
        clientes=conn.execute('SELECT * FROM clientes ORDER BY nombre').fetchall()
        conn.close()
        cm=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); cm.row_factory=sqlite3.Row
        vendedores=cm.execute('SELECT id,nombre,email FROM usuarios WHERE usuario_padre_id=? AND es_vendedor=1 AND activo=1 ORDER BY nombre',(uid,)).fetchall(); cm.close()
        tasas=obtener_tasas_usuario(uid)
        productos=[]
        for prod in pr:
            pd=dict(prod); precios=calcular_precios_todas_tasas(pd['precio_venta_usd'],tasas)
            iname=pd.get('imagen_principal') or obtener_imagen_producto(uid,pd['id'])
            iurl=f"/uploads/{uid}/{iname}" if iname else None
            productos.append({**pd,'precios':precios,'imagen_url':iurl})
        return render_template('user/ventas.html',productos=productos,vendedores=vendedores,ventas_recientes=vr,stats=stats,stats_mes=sm,tasas=tasas,clientes=clientes)
    except Exception as e: logging.error(f"Error ventas: {e}"); flash('Error','danger')
    return render_template('user/ventas.html',productos=[],vendedores=[],clientes=[])

@app.route('/user/procesar_venta',methods=['POST'])
@login_required
def procesar_venta():
    uid=session['user_id']
    limites=verificar_limites_usuario(uid)
    if limites['limite_ventas']>0:
        vm=ventas_mensuales_usuario(uid)
        if vm>=limites['limite_ventas']: return jsonify({'success':False,'error':f'Limite de {limites["limite_ventas"]} ventas.'})
    data=request.get_json()
    if not data or 'items' not in data: return jsonify({'success':False,'error':'Datos invalidos'})
    init_user_db(uid)
    conn=None
    try:
        dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=30); conn.row_factory=sqlite3.Row; conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('BEGIN')
        tasas=obtener_tasas_usuario(uid); vp=[]; tvu=0; vid=None
        tipo_venta=data.get('tipo_venta','contado')
        cliente_id=data.get('cliente_id'); cliente_nombre=data.get('cliente_nombre','Consumidor Final')
        monto_inicial_usd=float(data.get('monto_inicial_usd',0))
        monto_inicial_ves=float(data.get('monto_inicial_ves',0))
        for item in data['items']:
            pid=item['producto_id']; cant=int(item['cantidad']); pu=float(item['precio_usd']); tu=item.get('tasa_usada',tasas['activa'])
            p=conn.execute('SELECT nombre,stock_actual FROM productos WHERE id=?',(pid,)).fetchone()
            if not p: conn.rollback(); conn.close(); return jsonify({'success':False,'error':'Producto no encontrado'})
            if p['stock_actual']<cant: conn.rollback(); conn.close(); return jsonify({'success':False,'error':'Stock insuficiente'})
            total=cant*pu; tvu+=total
            conn.execute('UPDATE productos SET stock_actual=stock_actual-? WHERE id=?',(cant,pid))
            cur=conn.execute('''INSERT INTO ventas (producto_id,vendedor_id,cantidad,precio_usd,tasa_oficial,tasa_manual1,tasa_manual2,tasa_usada,total_usd,total_oficial,total_manual1,total_manual2,metodo_pago,observaciones,tipo_venta,cliente_id,cliente_nombre) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (pid,data.get('vendedor_id'),cant,pu,tasas['oficial'],tasas['manual1'],tasas['manual2'],tu,total,total*tasas['oficial'],total*tasas['manual1'],total*tasas['manual2'],data.get('metodo_pago','efectivo'),data.get('observaciones',''),tipo_venta,cliente_id,cliente_nombre))
            if vid is None: vid=cur.lastrowid
            vp.append({'producto':p['nombre'],'cantidad':cant,'precio_usd':pu,'total_usd':total,'nombre':p['nombre']})
        vn=session.get('user_name','Sistema')
        tasa_venta=tasas.get(tasas.get('activa','oficial'),0); tvu_ves=tvu*tasa_venta
        fp,fn,nf=generar_factura_venta(vid,vp,tasas,uid,vn,data.get('metodo_pago','Efectivo'),data.get('observaciones',''),tipo_venta,cliente_nombre,cliente_id)
        conn.execute('UPDATE ventas SET nro_factura=? WHERE id=?',(nf,vid))
        if tipo_venta=='contado': estado='pagado'; su=0; sv=0
        else: su=max(0,tvu-monto_inicial_usd); sv=max(0,tvu_ves-monto_inicial_ves); estado='pagado' if su<=0 else 'pendiente'
        cur_fc=conn.execute('INSERT INTO facturas_cobrar (venta_id,cliente_id,cliente_nombre,tipo_venta,total_usd,total_ves,saldo_usd,saldo_ves,estado,nro_factura) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (vid,cliente_id,cliente_nombre,tipo_venta,tvu,tvu_ves,su,sv,estado,nf))
        fid=cur_fc.lastrowid
        if monto_inicial_usd>0:
            conn.execute('INSERT INTO abonos (factura_id,monto_usd,monto_ves,tasa_usada,metodo_pago,notas) VALUES (?,?,?,?,?,?)',
                (fid,monto_inicial_usd,monto_inicial_ves,tvu_ves/tvu if tvu>0 else 36,'efectivo','Pago inicial'))
        conn.commit(); conn.close()
        logging.info(f"[OK] Venta #{vid}, Factura: {nf}, Tipo: {tipo_venta}, Estado: {estado}")
        return jsonify({'success':True,'message':'Venta procesada','ventas':vp,'total_venta_usd':tvu,'factura_url':f'/ver_factura/{fn}','nro_factura':nf,'tipo_venta':tipo_venta})
    except Exception as e:
        logging.error(f"Error procesar_venta: {e}")
        if conn:
            try: conn.rollback()
            except: pass
            try: conn.close()
            except: pass
        return jsonify({'success':False,'error':str(e)})

@app.route('/user/reportes')
@login_required
def user_reportes():
    uid=session['user_id']; init_user_db(uid)
    ventas_por_mes=[]; top_productos=[]; ventas_categoria=[]
    stats={'total_productos':0,'stock_total':0,'total_ventas':0,'ingresos_totales':0}
    try:
        dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20); conn.row_factory=sqlite3.Row
        ventas_por_mes=conn.execute("SELECT strftime('%Y-%m',creado_en) as mes, COUNT(*) as cantidad, COALESCE(SUM(total_usd),0) as total_usd, COALESCE(SUM(total_oficial),0) as total_oficial FROM ventas GROUP BY mes ORDER BY mes DESC LIMIT 12").fetchall()
        top_productos=conn.execute('SELECT p.nombre, COALESCE(SUM(v.cantidad),0) as total_vendido, COALESCE(SUM(v.total_usd),0) as ingresos FROM ventas v JOIN productos p ON v.producto_id=p.id GROUP BY p.id ORDER BY total_vendido DESC LIMIT 10').fetchall()
        ventas_categoria=conn.execute('SELECT p.categoria, COUNT(*) as cantidad, COALESCE(SUM(v.total_usd),0) as total_usd FROM ventas v JOIN productos p ON v.producto_id=p.id GROUP BY p.categoria ORDER BY cantidad DESC').fetchall()
        sr=conn.execute('''SELECT COALESCE((SELECT COUNT(*) FROM productos),0) as total_productos, COALESCE((SELECT SUM(stock_actual) FROM productos),0) as stock_total, COALESCE((SELECT COUNT(*) FROM ventas),0) as total_ventas, COALESCE((SELECT SUM(total_usd) FROM ventas),0) as ingresos_totales''').fetchone()
        if sr: stats={'total_productos':sr['total_productos']or 0,'stock_total':sr['stock_total']or 0,'total_ventas':sr['total_ventas']or 0,'ingresos_totales':sr['ingresos_totales']or 0}
        conn.close()
    except Exception as e: logging.error(f"Error reportes: {e}"); flash('Error','danger')
    return render_template('user/reportes.html',ventas_por_mes=ventas_por_mes,top_productos=top_productos,ventas_categoria=ventas_categoria,stats=stats)

@app.route('/user/clientes',methods=['GET','POST'])
@login_required
def gestionar_clientes():
    uid=session['user_id']; init_user_db(uid)
    if request.method=='POST':
        datos={'nombre':request.form.get('nombre','').strip(),'telefono':request.form.get('telefono','').strip(),'email':request.form.get('email','').strip(),'direccion':request.form.get('direccion','').strip(),'documento':request.form.get('documento','').strip()}
        if not datos['nombre']: flash('Nombre requerido','danger')
        else: agregar_cliente(uid,datos); flash('Cliente agregado','success')
        return redirect(url_for('gestionar_clientes'))
    clientes=obtener_clientes(uid)
    return render_template('user/clientes.html',clientes=clientes)

@app.route('/user/editar_cliente/<int:cliente_id>',methods=['POST'])
@login_required
def editar_cliente(cliente_id):
    uid=session['user_id']
    try:
        p=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(p,timeout=20)
        conn.execute('UPDATE clientes SET nombre=?,telefono=?,email=?,direccion=?,documento=? WHERE id=?',
            (request.form.get('nombre','').strip(),request.form.get('telefono','').strip(),request.form.get('email','').strip(),request.form.get('direccion','').strip(),request.form.get('documento','').strip(),cliente_id))
        conn.commit(); conn.close()
        flash('Cliente actualizado','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('gestionar_clientes'))

@app.route('/user/eliminar_cliente/<int:cliente_id>',methods=['POST'])
@login_required
def eliminar_cliente(cliente_id):
    uid=session['user_id']
    try:
        p=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(p,timeout=20); conn.row_factory=sqlite3.Row
        tiene=conn.execute('SELECT COUNT(*) as c FROM facturas_cobrar WHERE cliente_id=?',(cliente_id,)).fetchone()
        if tiene and tiene['c']>0: flash('No se puede eliminar: tiene facturas asociadas','danger')
        else: conn.execute('DELETE FROM clientes WHERE id=?',(cliente_id,)); conn.commit(); flash('Cliente eliminado','success')
        conn.close()
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('gestionar_clientes'))

# ============ RUTA DE FACTURAS ============
@app.route('/ver_factura/<filename>')
@login_required
def ver_factura(filename): return send_from_directory('facturas', filename)

@app.route('/user/facturas')
@login_required
def listar_facturas():
    uid=session['user_id']; init_user_db(uid)
    filtro=request.args.get('filtro','todas'); buscar=request.args.get('buscar','').strip()
    facturas_bd=obtener_facturas_cobrar(uid,filtro)
    facturas=[]
    for fc in facturas_bd:
        nro=fc.get('nro_factura','')
        cliente_info=fc.get('cliente_nombre','Consumidor Final')
        if fc.get('cliente_doc'): cliente_info+=f" - {fc['cliente_doc']}"
        facturas.append({'id':fc['id'],'nro_factura':nro,'fecha':fc.get('fecha_creacion','')[:10] if fc.get('fecha_creacion') else '','cliente':cliente_info,'total_usd':round(fc.get('total_usd',0),2),'total_ves':round(fc.get('total_ves',0),2),'saldo_usd':round(fc.get('saldo_usd',0),2),'saldo_ves':round(fc.get('saldo_ves',0),2),'estado':fc.get('estado','pagado'),'tipo_venta':fc.get('tipo_venta','contado'),'abonos':fc.get('abonos',[]),'total_abonado_usd':round(fc.get('total_abonado_usd',0),2),'filename':f"factura_{nro.replace('/','-')}_{uid}.html" if nro else '','url':f"/ver_factura/factura_{nro.replace('/','-')}_{uid}.html" if nro else ''})
    if buscar:
        bl=buscar.lower()
        facturas=[f for f in facturas if bl in str(f.get('nro_factura','')).lower() or bl in str(f.get('cliente','')).lower()]
    return render_template('user/facturas.html',facturas=facturas,filtro=filtro,buscar=buscar)

@app.route('/user/abonar_factura/<int:factura_id>',methods=['POST'])
@login_required
def abonar_factura(factura_id):
    uid=session['user_id']; filtro=request.args.get('filtro','todas')
    try: monto_usd=float(request.form.get('monto_usd',0)or 0)
    except: monto_usd=0
    try: monto_ves=float(request.form.get('monto_ves',0)or 0)
    except: monto_ves=0
    try: tasa_usada=float(request.form.get('tasa_usada',36)or 36)
    except: tasa_usada=36
    metodo=request.form.get('metodo_pago','efectivo'); notas=request.form.get('notas','')
    if monto_usd<=0: flash('Monto USD requerido','danger'); return redirect(url_for('listar_facturas',filtro=filtro))
    ok=agregar_abono(uid,factura_id,monto_usd,monto_ves,tasa_usada,metodo,notas)
    flash('Pago registrado correctamente' if ok else 'Error al registrar pago','success' if ok else 'danger')
    return redirect(url_for('listar_facturas',filtro=filtro))

# ============ RUTAS DE ADMINISTRADOR ============
@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    try:
        conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
        tu=conn.execute("SELECT COUNT(*) as c FROM usuarios WHERE role='user'").fetchone()['c']
        tv=conn.execute("SELECT COUNT(*) as c FROM usuarios WHERE es_vendedor=1").fetchone()['c']
        sa=conn.execute("SELECT COUNT(*) as c FROM suscripciones WHERE estado='activa' AND fecha_fin>=date('now')").fetchone()['c']
        ur=conn.execute("SELECT u.*, s.fecha_fin FROM usuarios u LEFT JOIN suscripciones s ON u.id=s.usuario_id AND s.estado='activa' WHERE u.role='user' ORDER BY u.fecha_registro DESC LIMIT 5").fetchall()
        conn.close()
        return render_template('admin/dashboard.html',total_usuarios=tu,total_vendedores=tv,suscripciones_activas=sa,usuarios_recientes=ur)
    except: flash('Error','danger'); return render_template('admin/dashboard.html')

@app.route('/admin/usuarios')
@login_required
@admin_required
def admin_usuarios():
    try:
        conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
        usuarios=conn.execute("SELECT u.*, s.fecha_fin, c.nombre as en, c.rif as er FROM usuarios u LEFT JOIN suscripciones s ON u.id=s.usuario_id AND s.estado='activa' LEFT JOIN config_empresa c ON u.id=c.usuario_id WHERE u.role='user' ORDER BY u.nombre").fetchall()
        conn.close()
        return render_template('admin/usuarios.html',usuarios=usuarios)
    except: flash('Error','danger'); return render_template('admin/usuarios.html',usuarios=[])

@app.route('/admin/suscripciones')
@login_required
@admin_required
def admin_suscripciones():
    try:
        conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
        sus=conn.execute('SELECT s.*, u.nombre as un FROM suscripciones s JOIN usuarios u ON s.usuario_id=u.id ORDER BY s.creado_en DESC').fetchall()
        us=conn.execute("SELECT id,nombre,email FROM usuarios WHERE role='user' AND (suscripcion_activa=0 OR suscripcion_activa IS NULL OR fecha_fin_suscripcion<date('now')) ORDER BY nombre").fetchall()
        conn.close()
        return render_template('admin/suscripciones.html',suscripciones=[dict(s) for s in sus],usuarios_sin_suscripcion=[dict(u) for u in us])
    except: flash('Error','danger'); return render_template('admin/suscripciones.html',suscripciones=[],usuarios_sin_suscripcion=[])

@app.route('/admin/crear_suscripcion',methods=['POST'])
@login_required
@admin_required
def crear_suscripcion():
    try:
        uid=request.form.get('usuario_id'); plan=request.form.get('plan'); dias=int(request.form.get('dias',30))
        if not uid or not plan: flash('Usuario y plan requeridos','danger'); return redirect(url_for('admin_suscripciones'))
        fi=date.today(); ff=fi+timedelta(days=dias)
        conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20)
        if conn.execute('SELECT id FROM suscripciones WHERE usuario_id=? AND estado="activa" AND fecha_fin>=date("now")',(uid,)).fetchone():
            flash('Ya tiene suscripcion activa','warning'); conn.close(); return redirect(url_for('admin_suscripciones'))
        conn.execute('INSERT INTO suscripciones (usuario_id,plan,dias,fecha_inicio,fecha_fin,monto,metodo_pago,notas,creado_por) VALUES (?,?,?,?,?,?,?,?,?)',
                   (uid,plan,dias,fi,ff,0,'admin','',session['user_id']))
        conn.execute('UPDATE usuarios SET suscripcion_activa=1,fecha_fin_suscripcion=?,limite_productos=?,limite_ventas_mensuales=? WHERE id=?',
                   (ff,obtener_limite_productos_por_plan(plan),obtener_limite_ventas_por_plan(plan),uid))
        conn.commit(); conn.close()
        flash(f'Suscripcion VIP creada. Vence: {ff}','success')
    except: flash('Error','danger')
    return redirect(url_for('admin_suscripciones'))

@app.route('/admin/cancelar_suscripcion/<int:sid>',methods=['POST'])
@login_required
@admin_required
def cancelar_suscripcion(sid):
    try:
        conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
        s=conn.execute('SELECT * FROM suscripciones WHERE id=?',(sid,)).fetchone()
        if not s: flash('No encontrada','danger'); conn.close(); return redirect(url_for('admin_suscripciones'))
        sd=dict(s)
        conn.execute('UPDATE suscripciones SET estado="cancelada",cancelado_en=date("now"),cancelado_por=? WHERE id=?',(session['user_id'],sid))
        conn.execute('UPDATE usuarios SET suscripcion_activa=0,limite_productos=50,limite_ventas_mensuales=20 WHERE id=?',(sd['usuario_id'],))
        conn.commit(); conn.close()
        flash('Suscripcion cancelada.','success')
    except: flash('Error','danger')
    return redirect(url_for('admin_suscripciones'))

@app.route('/admin/extender_suscripcion/<int:sid>',methods=['POST'])
@login_required
@admin_required
def extender_suscripcion(sid):
    try:
        de=int(request.form.get('dias_extra',30))
        conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
        s=conn.execute('SELECT * FROM suscripciones WHERE id=?',(sid,)).fetchone()
        if not s: flash('No encontrada','danger'); conn.close(); return redirect(url_for('admin_suscripciones'))
        sd=dict(s); nf=datetime.strptime(sd['fecha_fin'],'%Y-%m-%d')+timedelta(days=de)
        conn.execute('UPDATE suscripciones SET fecha_fin=?,dias=dias+? WHERE id=?',(nf.strftime('%Y-%m-%d'),de,sid))
        conn.execute('UPDATE usuarios SET fecha_fin_suscripcion=? WHERE id=?',(nf.strftime('%Y-%m-%d'),sd['usuario_id']))
        conn.commit(); conn.close()
        flash(f'Extendida {de} dias.','success')
    except: flash('Error','danger')
    return redirect(url_for('admin_suscripciones'))

@app.route('/admin/configurar_empresa/<int:uid>',methods=['GET','POST'])
@login_required
@admin_required
def admin_configurar_empresa(uid):
    conn=None
    try:
        conn=sqlite3.connect(DATABASE_PRINCIPAL,timeout=20); conn.row_factory=sqlite3.Row
        usuario=conn.execute('SELECT id,nombre,email FROM usuarios WHERE id=?',(uid,)).fetchone()
        if not usuario: flash('Usuario no encontrado','danger'); return redirect(url_for('admin_usuarios'))
        if request.method=='POST':
            datos={'nombre':request.form.get('nombre','').strip(),'rif':request.form.get('rif','').strip(),'direccion':request.form.get('direccion','').strip(),'telefono':request.form.get('telefono','').strip(),'email':request.form.get('email','').strip(),'mensaje_factura':request.form.get('mensaje_factura','').strip()}
            actualizar_config_empresa(uid,datos)
            flash('Configuracion actualizada','success'); return redirect(url_for('admin_configurar_empresa',uid=uid))
        config=obtener_config_empresa(uid); conn.close()
        return render_template('admin/configurar_empresa.html',usuario=dict(usuario),config=config)
    except: flash('Error','danger')
    if conn: conn.close()
    return redirect(url_for('admin_usuarios'))

# ============ RUTAS DE VENDEDOR ============
@app.route('/vendedor/ventas')
@login_required
@vendedor_required
def vendedor_ventas():
    vid=session['user_id']; upid=session.get('usuario_padre_id')
    if not upid: flash('Sin usuario padre','danger'); return redirect(url_for('logout'))
    init_user_db(upid)
    try:
        dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{upid}.db')
        conn=sqlite3.connect(dbp,timeout=20); conn.row_factory=sqlite3.Row
        pr=conn.execute('SELECT id,nombre,codigo,precio_venta_usd,stock_actual,categoria,imagen_principal FROM productos WHERE stock_actual>0 ORDER BY nombre').fetchall()
        vr=conn.execute('SELECT v.*, p.nombre as pn FROM ventas v JOIN productos p ON v.producto_id=p.id WHERE v.vendedor_id=? ORDER BY v.creado_en DESC LIMIT 10',(vid,)).fetchall()
        stats=conn.execute('SELECT COUNT(*) as t, COALESCE(SUM(total_usd),0) as i FROM ventas WHERE vendedor_id=?',(vid,)).fetchone()
        clientes=conn.execute('SELECT * FROM clientes ORDER BY nombre').fetchall()
        conn.close()
        tasas=obtener_tasas_usuario(upid)
        productos=[]
        for prod in pr:
            pd=dict(prod); precios=calcular_precios_todas_tasas(pd['precio_venta_usd'],tasas)
            iname=pd.get('imagen_principal') or obtener_imagen_producto(upid,pd['id'])
            iurl=f"/uploads/{upid}/{iname}" if iname else None
            productos.append({**pd,'precios':precios,'imagen_url':iurl})
        return render_template('vendedor/ventas.html',productos=productos,ventas_recientes=vr,stats=stats,tasas=tasas,clientes=clientes)
    except: flash('Error','danger'); return render_template('vendedor/ventas.html',productos=[],clientes=[])

@app.route('/vendedor/procesar_venta',methods=['POST'])
@login_required
@vendedor_required
def vendedor_procesar_venta():
    vid=session['user_id']; upid=session.get('usuario_padre_id')
    if not upid: return jsonify({'success':False,'error':'Sin usuario padre'})
    data=request.get_json()
    if not data or 'items' not in data: return jsonify({'success':False,'error':'Datos invalidos'})
    init_user_db(upid)
    conn=None
    try:
        dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{upid}.db')
        conn=sqlite3.connect(dbp,timeout=30); conn.row_factory=sqlite3.Row; conn.execute('PRAGMA busy_timeout=5000')
        conn.execute('BEGIN')
        tasas=obtener_tasas_usuario(upid); vp=[]; tvu=0; venta_id=None
        tipo_venta=data.get('tipo_venta','contado')
        cliente_id=data.get('cliente_id'); cliente_nombre=data.get('cliente_nombre','Consumidor Final')
        monto_inicial_usd=float(data.get('monto_inicial_usd',0))
        for item in data['items']:
            pid=item['producto_id']; cant=int(item['cantidad']); pu=float(item['precio_usd'])
            p=conn.execute('SELECT nombre,stock_actual FROM productos WHERE id=?',(pid,)).fetchone()
            if not p or p['stock_actual']<cant: conn.rollback(); conn.close(); return jsonify({'success':False,'error':'Stock insuficiente'})
            total=cant*pu; tvu+=total
            conn.execute('UPDATE productos SET stock_actual=stock_actual-? WHERE id=?',(cant,pid))
            cur=conn.execute('INSERT INTO ventas (producto_id,vendedor_id,cantidad,precio_usd,tasa_oficial,tasa_manual1,tasa_manual2,tasa_usada,total_usd,total_oficial,total_manual1,total_manual2,metodo_pago,observaciones,tipo_venta,cliente_id,cliente_nombre) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (pid,vid,cant,pu,tasas['oficial'],tasas['manual1'],tasas['manual2'],item.get('tasa_usada',tasas['activa']),total,total*tasas['oficial'],total*tasas['manual1'],total*tasas['manual2'],'efectivo',f'Vendedor {vid}',tipo_venta,cliente_id,cliente_nombre))
            if venta_id is None: venta_id=cur.lastrowid
            vp.append({'producto':p['nombre'],'cantidad':cant,'precio_usd':pu,'total_usd':total,'nombre':p['nombre']})
        vn=session.get('user_name','Vendedor')
        tasa_venta=tasas.get(tasas.get('activa','oficial'),0); tvu_ves=tvu*tasa_venta
        fp,fn,nf=generar_factura_venta(venta_id,vp,tasas,upid,vn,'Efectivo','',tipo_venta,cliente_nombre,cliente_id)
        conn.execute('UPDATE ventas SET nro_factura=? WHERE id=?',(nf,venta_id))
        if tipo_venta=='contado': estado='pagado'; su=0; sv=0
        else: su=max(0,tvu-monto_inicial_usd); sv=max(0,tvu_ves); estado='pagado' if su<=0 else 'pendiente'
        cur_fc=conn.execute('INSERT INTO facturas_cobrar (venta_id,cliente_id,cliente_nombre,tipo_venta,total_usd,total_ves,saldo_usd,saldo_ves,estado,nro_factura) VALUES (?,?,?,?,?,?,?,?,?,?)',
            (venta_id,cliente_id,cliente_nombre,tipo_venta,tvu,tvu_ves,su,sv,estado,nf))
        if monto_inicial_usd>0:
            conn.execute('INSERT INTO abonos (factura_id,monto_usd,monto_ves,tasa_usada,metodo_pago,notas) VALUES (?,?,?,?,?,?)',
                (cur_fc.lastrowid,monto_inicial_usd,0,tvu_ves/tvu if tvu>0 else 36,'efectivo','Pago inicial'))
        conn.commit(); conn.close()
        return jsonify({'success':True,'message':'Venta procesada','ventas':vp,'factura_url':f'/ver_factura/{fn}','nro_factura':nf,'tipo_venta':tipo_venta})
    except Exception as e:
        logging.error(f"Error: {e}")
        if conn: conn.rollback(); conn.close()
        return jsonify({'success':False,'error':str(e)})

# ============ API TASAS ============
@app.route('/api/tasa_bcv')
def api_tasa_bcv():
    try: return jsonify({'success':True,'tasa':obtener_tasa_bcv()})
    except: return jsonify({'success':False,'tasa':36})

@app.route('/user/actualizar_tasa_oficial',methods=['POST'])
@login_required
def actualizar_tasa_oficial():
    uid=session['user_id']
    try:
        t=obtener_tasa_bcv(); init_user_db(uid)
        dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20)
        conn.execute('INSERT INTO historial_tasas (tasa_oficial,cambio_por) VALUES (?,?)',(t,'BCV'))
        conn.execute("UPDATE config_tasa SET tasa_oficial=?,tasa_activa='oficial',fuente_oficial='BCV',actualizado_en=CURRENT_TIMESTAMP",(t,))
        conn.commit(); conn.close(); session['tasa_activa']='oficial'
        return jsonify({'success':True,'tasa':t})
    except: return jsonify({'success':False})

@app.route('/user/configurar_tasas',methods=['POST'])
@login_required
def configurar_tasas():
    uid=session['user_id']; data=request.get_json()
    if not data.get('tasa_manual1') or not data.get('tasa_manual2'): return jsonify({'success':False})
    try:
        init_user_db(uid); dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
        conn=sqlite3.connect(dbp,timeout=20)
        conn.execute('INSERT INTO historial_tasas (tasa_manual1,tasa_manual2,tasa_activa,cambio_por) VALUES (?,?,?,?)',(data['tasa_manual1'],data['tasa_manual2'],data.get('tasa_activa','oficial'),'usuario'))
        conn.execute('UPDATE config_tasa SET tasa_manual1=?,tasa_manual2=?,tasa_activa=?,actualizado_en=CURRENT_TIMESTAMP',(float(data['tasa_manual1']),float(data['tasa_manual2']),data.get('tasa_activa','oficial')))
        conn.commit(); conn.close(); session['tasa_activa']=data.get('tasa_activa','oficial')
        return jsonify({'success':True})
    except: return jsonify({'success':False})

@app.route('/api/actualizar_tasa_ahora')
@login_required
def actualizar_tasa_ahora():
    try:
        cf='tasa_cache.json'
        if os.path.exists(cf): os.remove(cf)
        t=obtener_tasa_bcv(); uid=session['user_id']
        if uid and not session.get('es_vendedor'):
            init_user_db(uid); dbp=os.path.join(app.config['DATABASE_DIR'],f'user_{uid}.db')
            conn=sqlite3.connect(dbp,timeout=20)
            conn.execute('INSERT INTO historial_tasas (tasa_oficial,cambio_por) VALUES (?,?)',(t,'Manual'))
            conn.execute("UPDATE config_tasa SET tasa_oficial=?,fuente_oficial='BCV',actualizado_en=CURRENT_TIMESTAMP",(t,))
            conn.commit(); conn.close()
        return jsonify({'success':True,'tasa':t})
    except: return jsonify({'success':False})

@app.route('/api/obtener_subcategorias')
def obtener_subcategorias():
    return jsonify({'success':True,'subcategorias':obtener_subcategorias_por_categoria(request.args.get('categoria',''))})

@app.route('/uploads/<path:filename>')
def uploaded_files(filename): return send_from_directory(app.config['UPLOAD_FOLDER'],filename)

# ============ ERROR HANDLERS ============
@app.errorhandler(404)
def not_found(error): return render_template('error.html',error='Pagina no encontrada',codigo=404),404
@app.errorhandler(500)
def internal_error(error): logging.error(f"Error 500: {error}"); return render_template('error.html',error='Error interno',codigo=500),500
@app.errorhandler(403)
def forbidden(error): return render_template('error.html',error='Acceso denegado',codigo=403),403

# ============ INICIALIZACION ============
if __name__=='__main__':
    with app.app_context(): init_main_db(); logging.info("Aplicacion inicializada")
    app.run(debug=os.environ.get('FLASK_ENV')=='development',host='0.0.0.0',port=int(os.environ.get('PORT',5000)),threaded=True)
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
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from PIL import Image
import uuid
from contextlib import contextmanager
import re

# ============ CONFIGURACIÓN DE LOGGING ============
def setup_logging():
    if not os.path.exists('logs'):
        os.mkdir('logs')
    
    file_handler = RotatingFileHandler(
        'logs/sistema_gestion.log',
        maxBytes=10240,
        backupCount=10,
        encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    file_handler.setLevel(logging.INFO)
    
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s'
    ))
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
    if not os.path.exists(directorio):
        os.makedirs(directorio)
        logging.info(f"Directorio creado: {directorio}")

DATABASE_PRINCIPAL = os.path.join(app.config['DATABASE_DIR'], 'sistema_gestion.db')

# ============ FUNCIONES AUXILIARES ============
def row_to_dict(row):
    if row is None: return {}
    if isinstance(row, dict): return row
    try: return dict(row)
    except: return {k: row[k] for k in row.keys()}

# ============ CONTEXT MANAGERS ============
@contextmanager
def get_main_db():
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = WAL')
        yield conn
        conn.commit()
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error BD principal: {e}")
        raise
    finally:
        if conn: conn.close()

@contextmanager
def get_user_db(user_id):
    conn = None
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        conn.execute('PRAGMA journal_mode = WAL')
        yield conn
        conn.commit()
    except Exception as e:
        if conn: conn.rollback()
        logging.error(f"Error BD usuario {user_id}: {e}")
        raise
    finally:
        if conn: conn.close()

# ============ FUNCIONES DE IMÁGENES ============
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

def generar_nombre_archivo_seguro(filename, user_id, producto_id=None):
    extension = filename.rsplit('.', 1)[1].lower() if '.' in filename else 'jpg'
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    random_str = uuid.uuid4().hex[:8]
    nombre_base = f"user_{user_id}_prod_{producto_id}_{timestamp}_{random_str}" if producto_id else f"user_{user_id}_{timestamp}_{random_str}"
    return f"{nombre_base}.{extension}"

def guardar_imagen_producto(file, user_id, producto_id):
    if not file or file.filename == '' or not allowed_file(file.filename): return None
    try:
        filename = generar_nombre_archivo_seguro(file.filename, user_id, producto_id)
        user_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id))
        if not os.path.exists(user_upload_dir): os.makedirs(user_upload_dir)
        filepath = os.path.join(user_upload_dir, filename)
        file.save(filepath)
        try:
            img = Image.open(filepath); img.thumbnail((300, 300))
            thumb_path = os.path.join(user_upload_dir, f"thumb_{filename}")
            img.save(thumb_path, quality=85, optimize=True)
        except: pass
        logging.info(f"Imagen guardada: {filename}")
        return filename
    except Exception as e:
        logging.error(f"Error guardando imagen: {e}")
        return None

def obtener_imagen_producto(user_id, producto_id):
    try:
        user_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id))
        if not os.path.exists(user_upload_dir): return None
        import glob
        patron = f"*user_{user_id}_prod_{producto_id}_*"
        imagenes = glob.glob(os.path.join(user_upload_dir, patron))
        for img in imagenes:
            if 'thumb_' in os.path.basename(img): return os.path.basename(img)
        if imagenes: return os.path.basename(imagenes[0])
        return None
    except Exception as e:
        logging.error(f"Error obteniendo imagen: {e}")
        return None

def eliminar_imagenes_producto(user_id, producto_id):
    try:
        user_upload_dir = os.path.join(app.config['UPLOAD_FOLDER'], str(user_id))
        if not os.path.exists(user_upload_dir): return
        import glob
        patron = f"*user_{user_id}_prod_{producto_id}_*"
        for filepath in glob.glob(os.path.join(user_upload_dir, patron)):
            try: os.remove(filepath)
            except: pass
    except Exception as e: logging.error(f"Error eliminando imagenes: {e}")

# ============ CATEGORÍAS Y MARCAS ============
def obtener_categorias_predefinidas():
    return ['Repuestos Moto', 'Electronica', 'Hogar', 'Ropa', 'Herramientas', 'Automotriz', 'Oficina', 'Alimentos', 'Bebidas', 'Juguetes', 'Deportes', 'Salud y Belleza', 'Libros', 'Construccion', 'Farmacia', 'Otros']

def obtener_subcategorias_por_categoria(categoria):
    subcategorias = {
        'Repuestos Moto': ['Motor', 'Transmision', 'Frenos', 'Suspension', 'Electricidad', 'Chasis', 'Accesorios', 'Llantas y Neumaticos', 'Carroceria', 'Escape', 'Combustible', 'Refrigeracion', 'Lubricacion', 'Faros y Luces', 'Asientos', 'Manillares', 'Otros Repuestos'],
        'Electronica': ['Telefonos', 'Computadoras', 'Tablets', 'Audio', 'Video', 'Camaras', 'Accesorios'],
        'Automotriz': ['Repuestos Auto', 'Accesorios Auto', 'Lubricantes', 'Llantas', 'Baterias'],
        'Herramientas': ['Electricas', 'Manuales', 'Medicion', 'Jardineria']
    }
    return subcategorias.get(categoria, [])

def obtener_marcas_moto():
    marcas = sorted(['Bera', 'Empire Keeway', 'Hero', 'Honda', 'Kawasaki', 'Suzuki', 'TVS', 'Yamaha'])
    marcas.append('Otra Marca')
    return marcas

# ============ FUNCIONES DE BASE DE DATOS ============
def init_main_db():
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS usuarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                telefono TEXT,
                role TEXT DEFAULT 'user',
                fecha_registro DATETIME DEFAULT CURRENT_TIMESTAMP,
                activo BOOLEAN DEFAULT TRUE,
                es_vendedor BOOLEAN DEFAULT FALSE,
                usuario_padre_id INTEGER,
                limite_productos INTEGER DEFAULT 50,
                limite_ventas_mensuales INTEGER DEFAULT 20,
                tasa_activa TEXT DEFAULT 'oficial',
                suscripcion_activa BOOLEAN DEFAULT FALSE,
                fecha_fin_suscripcion DATE,
                FOREIGN KEY (usuario_padre_id) REFERENCES usuarios (id) ON DELETE SET NULL
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS suscripciones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER NOT NULL,
                plan TEXT NOT NULL,
                dias INTEGER NOT NULL,
                fecha_inicio DATE NOT NULL,
                fecha_fin DATE NOT NULL,
                monto REAL NOT NULL DEFAULT 0,
                estado TEXT DEFAULT 'activa',
                metodo_pago TEXT,
                transaccion_id TEXT,
                notas TEXT,
                creado_por INTEGER,
                cancelado_en DATE,
                cancelado_por INTEGER,
                creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE,
                FOREIGN KEY (creado_por) REFERENCES usuarios (id) ON DELETE SET NULL
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS auditoria (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER,
                accion TEXT NOT NULL,
                tabla_afectada TEXT,
                registro_id INTEGER,
                detalles TEXT,
                ip_address TEXT,
                user_agent TEXT,
                creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE SET NULL
            )
        ''')
        
        conn.execute('''
            CREATE TABLE IF NOT EXISTS config_empresa (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id INTEGER UNIQUE NOT NULL,
                nombre TEXT NOT NULL DEFAULT 'Mi Empresa',
                rif TEXT DEFAULT 'J-00000000-0',
                direccion TEXT DEFAULT 'Venezuela',
                telefono TEXT DEFAULT '',
                email TEXT DEFAULT '',
                logo TEXT DEFAULT '',
                mensaje_factura TEXT DEFAULT 'Gracias por su compra!',
                creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
                actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (usuario_id) REFERENCES usuarios (id) ON DELETE CASCADE
            )
        ''')
        
        try: conn.execute('ALTER TABLE usuarios ADD COLUMN suscripcion_activa BOOLEAN DEFAULT FALSE')
        except: pass
        try: conn.execute('ALTER TABLE usuarios ADD COLUMN fecha_fin_suscripcion DATE')
        except: pass
        try: conn.execute('ALTER TABLE usuarios ADD COLUMN limite_productos INTEGER DEFAULT 50')
        except: pass
        try: conn.execute('ALTER TABLE usuarios ADD COLUMN limite_ventas_mensuales INTEGER DEFAULT 20')
        except: pass
        
        admin = conn.execute("SELECT id FROM usuarios WHERE email = 'admin@sistema.com'").fetchone()
        if not admin:
            password_hash = generate_password_hash('Admin123!')
            conn.execute('INSERT INTO usuarios (nombre, email, password_hash, role, activo) VALUES (?,?,?,?,?)',
                       ('Administrador', 'admin@sistema.com', password_hash, 'admin', 1))
            user_hash = generate_password_hash('User123!')
            conn.execute('INSERT INTO usuarios (nombre, email, password_hash, telefono, activo) VALUES (?,?,?,?,?)',
                       ('Usuario Demo', 'demo@demo.com', user_hash, '04141234567', 1))
        
        conn.commit()
        logging.info("Base de datos principal inicializada correctamente")
    except sqlite3.Error as e:
        logging.error(f"Error inicializando BD principal: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def init_user_db(user_id):
    db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
    if os.path.exists(db_path): ensure_user_db_tables(user_id); return
    conn = None
    try:
        conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
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
            observaciones TEXT, nro_factura TEXT, creado_en DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (producto_id) REFERENCES productos (id) ON DELETE RESTRICT)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS gastos (
            id INTEGER PRIMARY KEY AUTOINCREMENT, descripcion TEXT NOT NULL,
            categoria TEXT NOT NULL, monto_usd REAL NOT NULL, monto_oficial REAL,
            monto_manual1 REAL, monto_manual2 REAL, fecha DATE DEFAULT CURRENT_DATE,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS config_tasa (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tasa_oficial REAL DEFAULT 36.00,
            tasa_manual1 REAL DEFAULT 38.00, tasa_manual2 REAL DEFAULT 40.00,
            tasa_activa TEXT DEFAULT 'oficial', fuente_oficial TEXT DEFAULT 'BCV',
            actualizado_en DATETIME DEFAULT CURRENT_TIMESTAMP, actualizado_por INTEGER)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS historial_tasas (
            id INTEGER PRIMARY KEY AUTOINCREMENT, tasa_oficial REAL, tasa_manual1 REAL,
            tasa_manual2 REAL, tasa_activa TEXT, cambio_por TEXT,
            creado_en DATETIME DEFAULT CURRENT_TIMESTAMP)''')
        try: conn.execute('ALTER TABLE ventas ADD COLUMN nro_factura TEXT')
        except: pass
        conn.execute('INSERT OR IGNORE INTO config_tasa (id, tasa_oficial, tasa_manual1, tasa_manual2, tasa_activa, fuente_oficial) VALUES (1, 36.00, 38.00, 40.00, "oficial", "BCV")')
        conn.commit()
        logging.info(f"Base de datos creada para usuario {user_id}")
    except sqlite3.Error as e:
        logging.error(f"Error creando BD para usuario {user_id}: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

def ensure_user_db_tables(user_id):
    db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
    if not os.path.exists(db_path): init_user_db(user_id); return
    conn = None
    try:
        conn = sqlite3.connect(db_path); conn.row_factory = sqlite3.Row
        cursor = conn.cursor(); cursor.execute("PRAGMA table_info(productos)")
        columnas = [col[1] for col in cursor.fetchall()]
        for col, tipo in [('imagen_principal', 'TEXT'), ('medida_unidad', 'TEXT DEFAULT "Unidad"'), ('medida_cantidad', 'REAL DEFAULT 1')]:
            if col not in columnas:
                try: conn.execute(f'ALTER TABLE productos ADD COLUMN {col} {tipo}')
                except: pass
        try: conn.execute('ALTER TABLE ventas ADD COLUMN nro_factura TEXT')
        except: pass
        if conn.execute('SELECT COUNT(*) as count FROM config_tasa').fetchone()['count'] == 0:
            conn.execute('INSERT INTO config_tasa (tasa_oficial, tasa_manual1, tasa_manual2, tasa_activa, fuente_oficial) VALUES (36.00, 38.00, 40.00, "oficial", "BCV")')
        conn.commit()
    except Exception as e:
        logging.error(f"Error asegurando tablas: {e}")
        if conn: conn.rollback()
    finally:
        if conn: conn.close()

# ============ FUNCIONES DE SUSCRIPCIONES ============
def obtener_suscripcion_activa(usuario_id):
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        s = conn.execute("SELECT s.*, u.nombre as usuario_nombre, u.email as usuario_email FROM suscripciones s JOIN usuarios u ON s.usuario_id=u.id WHERE s.usuario_id=? AND s.estado='activa' AND s.fecha_fin >= date('now') ORDER BY s.fecha_fin DESC LIMIT 1", (usuario_id,)).fetchone()
        conn.close()
        return dict(s) if s else None
    except Exception as e:
        logging.error(f"Error suscripcion: {e}")
        return None

def verificar_limites_usuario(usuario_id):
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        u = conn.execute('SELECT id, nombre, suscripcion_activa, fecha_fin_suscripcion, limite_productos, limite_ventas_mensuales FROM usuarios WHERE id=?', (usuario_id,)).fetchone()
        conn.close()
        if not u: return {'suscripcion_activa': False, 'limite_productos': 50, 'limite_ventas': 20, 'plan': 'Free'}
        activa = False
        if u['suscripcion_activa'] and u['fecha_fin_suscripcion']:
            if u['fecha_fin_suscripcion'] >= str(date.today()): activa = True
        return {
            'suscripcion_activa': activa,
            'limite_productos': u['limite_productos'] or 50,
            'limite_ventas': u['limite_ventas_mensuales'] or 20,
            'fecha_fin': u['fecha_fin_suscripcion'],
            'plan': 'VIP' if activa else 'Free'
        }
    except Exception as e:
        logging.error(f"Error limites: {e}")
        return {'suscripcion_activa': False, 'limite_productos': 50, 'limite_ventas': 20, 'plan': 'Free'}

def cantidad_productos_usuario(usuario_id):
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{usuario_id}.db')
        if not os.path.exists(db_path): return 0
        conn = sqlite3.connect(db_path, timeout=20)
        c = conn.execute('SELECT COUNT(*) as count FROM productos').fetchone()['count']
        conn.close(); return c
    except: return 0

def ventas_mensuales_usuario(usuario_id):
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{usuario_id}.db')
        if not os.path.exists(db_path): return 0
        conn = sqlite3.connect(db_path, timeout=20)
        c = conn.execute("SELECT COUNT(*) as count FROM ventas WHERE strftime('%Y-%m', creado_en) = strftime('%Y-%m', 'now')").fetchone()['count']
        conn.close(); return c
    except: return 0

def obtener_limite_productos_por_plan(plan):
    return 999999 if plan.lower() == 'vip' else 50

def obtener_limite_ventas_por_plan(plan):
    return 999999 if plan.lower() == 'vip' else 20

def registrar_auditoria(usuario_id, accion, tabla, registro_id=None, detalles=None):
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20)
        conn.execute('INSERT INTO auditoria (usuario_id, accion, tabla_afectada, registro_id, detalles) VALUES (?,?,?,?,?)',
                   (usuario_id, accion, tabla, registro_id, detalles))
        conn.commit(); conn.close()
    except Exception as e: logging.error(f"Error auditoria: {e}")

# ============ FUNCIONES DE CONFIGURACION DE EMPRESA ============
def obtener_config_empresa(usuario_id=None):
    if usuario_id is None: usuario_id = session.get('user_id')
    if usuario_id is None: return {'nombre': 'Mi Empresa', 'rif': 'J-00000000-0', 'direccion': 'Venezuela', 'telefono': '', 'email': '', 'mensaje_factura': 'Gracias por su compra!'}
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        c = conn.execute('SELECT * FROM config_empresa WHERE usuario_id=?', (usuario_id,)).fetchone()
        conn.close()
        if c: return dict(c)
    except: pass
    return {'nombre': 'Mi Empresa', 'rif': 'J-00000000-0', 'direccion': 'Venezuela', 'telefono': '', 'email': '', 'mensaje_factura': 'Gracias por su compra!'}

def actualizar_config_empresa(usuario_id, datos):
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20)
        if conn.execute('SELECT id FROM config_empresa WHERE usuario_id=?', (usuario_id,)).fetchone():
            conn.execute('UPDATE config_empresa SET nombre=?, rif=?, direccion=?, telefono=?, email=?, mensaje_factura=?, actualizado_en=CURRENT_TIMESTAMP WHERE usuario_id=?',
                       (datos.get('nombre','Mi Empresa'), datos.get('rif','J-00000000-0'), datos.get('direccion','Venezuela'), datos.get('telefono',''), datos.get('email',''), datos.get('mensaje_factura','Gracias por su compra!'), usuario_id))
        else:
            conn.execute('INSERT INTO config_empresa (usuario_id, nombre, rif, direccion, telefono, email, mensaje_factura) VALUES (?,?,?,?,?,?,?)',
                       (usuario_id, datos.get('nombre','Mi Empresa'), datos.get('rif','J-00000000-0'), datos.get('direccion','Venezuela'), datos.get('telefono',''), datos.get('email',''), datos.get('mensaje_factura','Gracias por su compra!')))
        conn.commit(); conn.close(); return True
    except Exception as e:
        logging.error(f"Error: {e}"); return False

# ============ FUNCION DE FACTURA ============
def generar_factura_venta(venta_id, productos_vendidos, tasas, user_id, vendedor_nombre, metodo_pago, observaciones):
    config = obtener_config_empresa(user_id)
    fecha = datetime.now(); fecha_str = fecha.strftime('%d/%m/%Y %H:%M:%S')
    nro_factura = f"F-{fecha.strftime('%Y%m%d%H%M%S')}-{venta_id}"
    total_usd = sum(item['cantidad']*item['precio_usd'] for item in productos_vendidos)
    tasa_activa_valor = tasas.get(tasas.get('activa','oficial'), 0); total_ves = total_usd * tasa_activa_valor
    html = f'''<!DOCTYPE html><html lang="es"><head><meta charset="UTF-8"><title>Factura {nro_factura}</title>
<style>@page{{size:A4;margin:1cm}}body{{font-family:Arial,sans-serif;font-size:12px;margin:0;padding:20px;color:#333}}
.header{{text-align:center;border-bottom:2px solid #1e3a5f;padding-bottom:15px;margin-bottom:15px}}
.header h1{{margin:0;font-size:22px;color:#1e3a5f;text-transform:uppercase}}
.header .rif{{font-size:12px;color:#666}}.header .direccion{{font-size:11px;color:#666}}
.header .contacto{{font-size:10px;color:#888}}
.factura-info{{display:flex;justify-content:space-between;margin-bottom:15px;font-size:11px}}
.factura-info strong{{color:#1e3a5f}}
table.items{{width:100%;border-collapse:collapse;margin:15px 0;font-size:11px}}
table.items thead th{{background:#1e3a5f;color:white;padding:8px 10px;text-align:left;font-weight:600;font-size:10px;text-transform:uppercase}}
table.items tbody td{{padding:8px 10px;border-bottom:1px solid #e2e8f0}}
.text-right{{text-align:right}}.text-center{{text-align:center}}
.totales{{width:100%;max-width:300px;margin-left:auto;margin-top:10px}}
.totales table{{width:100%;font-size:12px}}.totales td{{padding:5px 10px}}
.totales .total-final{{font-size:16px;font-weight:bold;color:#1e3a5f;border-top:2px solid #1e3a5f}}
.tasa-info{{background:#fef3c7;border:1px solid #f59e0b;border-radius:6px;padding:8px 12px;margin:10px 0;font-size:10px;text-align:center}}
.footer{{text-align:center;border-top:2px solid #1e3a5f;padding-top:10px;margin-top:20px;font-size:10px;color:#666}}
.footer .mensaje{{font-size:12px;font-weight:bold;color:#1e3a5f;margin-bottom:5px}}
@media print{{body{{-webkit-print-color-adjust:exact;print-color-adjust:exact}}.no-print{{display:none}}}}</style></head><body>
<div class="header"><h1>{config.get('nombre','Mi Empresa')}</h1><div class="rif">RIF: {config.get('rif','J-00000000-0')}</div><div class="direccion">{config.get('direccion','Venezuela')}</div><div class="contacto">Tel: {config.get('telefono','N/A')} | Email: {config.get('email','N/A')}</div></div>
<div class="factura-info"><div><strong>FACTURA:</strong> {nro_factura}<br><strong>Fecha:</strong> {fecha_str}<br><strong>Vendedor:</strong> {vendedor_nombre}</div><div><strong>Pago:</strong> {metodo_pago.upper()}<br><strong>Tasa:</strong> {tasas.get('activa','Oficial').upper()}<br><strong>Cliente:</strong> Consumidor Final</div></div>
<table class="items"><thead><tr><th class="text-center">Cant</th><th>Producto</th><th class="text-right">P.Unit(USD)</th><th class="text-right">Total(USD)</th><th class="text-right">Total(VES)</th></tr></thead><tbody>'''
    for item in productos_vendidos:
        sub = item['cantidad']*item['precio_usd']; sv = sub*tasa_activa_valor
        html += f'<tr><td class="text-center">{item["cantidad"]}</td><td>{item["nombre"]}</td><td class="text-right">${item["precio_usd"]:.2f}</td><td class="text-right">${sub:.2f}</td><td class="text-right">Bs.{sv:.2f}</td></tr>'
    html += f'''</tbody></table>
<div class="totales"><table><tr><td><strong>Total USD:</strong></td><td class="text-right">${total_usd:.2f}</td></tr><tr><td><strong>Total VES:</strong></td><td class="text-right total-final">Bs.{total_ves:.2f}</td></tr></table></div>
<div class="tasa-info"><strong>Tasa:</strong> {tasas.get('activa','Oficial').upper()} | <strong>Valor:</strong> Bs.{tasa_activa_valor:.2f}/USD</div>
{f'<div style="background:#f8fafc;border:1px solid #e2e8f0;border-radius:6px;padding:10px 15px;margin:10px 0;font-size:11px"><strong>Obs:</strong> {observaciones}</div>' if observaciones else ''}
<div class="footer"><div class="mensaje">{config.get('mensaje_factura','Gracias por su compra!')}</div><p>Generado el {fecha_str}</p></div>
<div class="no-print" style="text-align:center;margin-top:20px"><button onclick="window.print()" style="background:#1e3a5f;color:white;border:none;padding:10px 25px;border-radius:6px;cursor:pointer;font-size:14px">Imprimir Factura</button></div></body></html>'''
    facturas_dir = os.path.join('facturas')
    if not os.path.exists(facturas_dir): os.makedirs(facturas_dir)
    filename = f"factura_{nro_factura.replace('/','-')}_{user_id}.html"
    filepath = os.path.join(facturas_dir, filename)
    with open(filepath, 'w', encoding='utf-8') as f: f.write(html)
    return filepath, filename, nro_factura

# ============ FUNCIONES DE TASAS ============
def obtener_tasas_usuario(user_id):
    default = {'oficial':36,'manual1':38,'manual2':40,'activa':'oficial','fuente_oficial':'BCV','actualizado_en':''}
    try:
        init_user_db(user_id)
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        c = conn.execute('SELECT * FROM config_tasa ORDER BY id DESC LIMIT 1').fetchone(); conn.close()
        if c:
            cd = dict(c)
            return {'oficial':float(cd.get('tasa_oficial',36)or 36),'manual1':float(cd.get('tasa_manual1',38)or 38),'manual2':float(cd.get('tasa_manual2',40)or 40),'activa':cd.get('tasa_activa')or'oficial','fuente_oficial':cd.get('fuente_oficial')or'BCV','actualizado_en':cd.get('actualizado_en')or''}
    except: pass
    return default

def calcular_precios_todas_tasas(precio_usd, tasas):
    if precio_usd is None: precio_usd = 0
    precio_usd = float(precio_usd)
    return {'usd':round(precio_usd,2),'oficial':round(precio_usd*float(tasas.get('oficial',36)),2),'manual1':round(precio_usd*float(tasas.get('manual1',38)),2),'manual2':round(precio_usd*float(tasas.get('manual2',40)),2)}

def obtener_tasa_bcv():
    cache_file = 'tasa_cache.json'
    if os.path.exists(cache_file):
        try:
            with open(cache_file,'r',encoding='utf-8') as f:
                cache = json.load(f)
                if (datetime.now()-datetime.fromtimestamp(cache.get('timestamp',0))).seconds < 3600: return float(cache['tasa'])
        except: pass
    try:
        from bs4 import BeautifulSoup
        r = requests.get('http://www.bcv.org.ve/', headers={'User-Agent':'Mozilla/5.0'}, timeout=15, verify=False)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            d = soup.find('div',{'id':'dolar'})
            if d:
                s = d.find('strong', class_='strong-tb')
                if s:
                    tasa = float(s.text.strip().replace('.','').replace(',','.'))
                    if 10 < tasa < 1000:
                        with open(cache_file,'w',encoding='utf-8') as f: json.dump({'tasa':tasa,'fuente':'BCV','timestamp':datetime.now().timestamp()}, f)
                        return tasa
    except: pass
    try:
        r = requests.get('https://api.exchangerate-api.com/v4/latest/USD', timeout=10)
        if r.status_code == 200:
            tasa = float(r.json().get('rates',{}).get('VES',0))
            if 10 < tasa < 1000:
                with open(cache_file,'w',encoding='utf-8') as f: json.dump({'tasa':tasa,'fuente':'ExchangeRate','timestamp':datetime.now().timestamp()}, f)
                return tasa
    except: pass
    if os.path.exists(cache_file):
        try:
            with open(cache_file,'r',encoding='utf-8') as f: return float(json.load(f).get('tasa',36))
        except: pass
    return 36.00

# ============ DECORADORES ============
def login_required(f):
    @wraps(f)
    def decorated(*a,**k):
        if 'user_id' not in session: flash('Inicia sesion','warning'); return redirect(url_for('login'))
        return f(*a,**k)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*a,**k):
        if session.get('role')!='admin': flash('Acceso denegado','danger'); return redirect(url_for('index'))
        return f(*a,**k)
    return decorated

def vendedor_required(f):
    @wraps(f)
    def decorated(*a,**k):
        if not session.get('es_vendedor'): flash('Acceso denegado','danger'); return redirect(url_for('index'))
        return f(*a,**k)
    return decorated

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

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email','').strip(); password = request.form.get('password','')
        if not email or not password: flash('Email y contrasena requeridos','danger'); return render_template('login.html')
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
            user = conn.execute('SELECT * FROM usuarios WHERE email=?',(email,)).fetchone()
            if not user: flash('Credenciales invalidas','danger'); conn.close(); return render_template('login.html')
            ud = dict(user)
            if not check_password_hash(ud['password_hash'], password): flash('Credenciales invalidas','danger'); conn.close(); return render_template('login.html')
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

@app.route('/register', methods=['GET','POST'])
def register():
    if request.method == 'POST':
        nombre = request.form.get('nombre','').strip(); email = request.form.get('email','').strip()
        password = request.form.get('password',''); telefono = request.form.get('telefono','').strip()
        if not nombre or not email or not password: flash('Todos los campos obligatorios','danger'); return render_template('register.html')
        if len(password)<6: flash('Minimo 6 caracteres','danger'); return render_template('register.html')
        conn = None
        try:
            conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
            if conn.execute('SELECT id FROM usuarios WHERE email=?',(email,)).fetchone(): flash('Email ya registrado','danger'); conn.close(); return render_template('register.html')
            ph = generate_password_hash(password)
            cursor = conn.execute('INSERT INTO usuarios (nombre, email, password_hash, telefono) VALUES (?,?,?,?)',(nombre,email,ph,telefono))
            user_id = cursor.lastrowid; conn.commit(); conn.close()
            init_user_db(user_id)
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
    user_id = session['user_id']; init_user_db(user_id)
    limites = {'suscripcion_activa':False,'limite_productos':50,'limite_ventas':20,'plan':'Free'}
    suscripcion = None; config_empresa = {}; tasa_bcv = 36.00
    tasas = {'oficial':36,'manual1':38,'manual2':40,'activa':'oficial'}
    stats = {'total_productos':0,'productos_bajo_stock':0,'ventas_mes':0,'ingresos_mes_usd':0}
    alertas_stock = []; ventas_por_dia = []; ventas_categoria = []; meses_anteriores = []
    try:
        limites = verificar_limites_usuario(user_id); suscripcion = obtener_suscripcion_activa(user_id)
        config_empresa = obtener_config_empresa(user_id); tasa_bcv = obtener_tasa_bcv(); tasas = obtener_tasas_usuario(user_id)
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        stats = conn.execute('''SELECT (SELECT COUNT(*) FROM productos) as total_productos,
            (SELECT COUNT(*) FROM productos WHERE stock_actual<=stock_minimo) as productos_bajo_stock,
            (SELECT COUNT(*) FROM ventas WHERE strftime("%Y-%m",creado_en)=strftime("%Y-%m","now")) as ventas_mes,
            (SELECT COALESCE(SUM(total_usd),0) FROM ventas WHERE strftime("%Y-%m",creado_en)=strftime("%Y-%m","now")) as ingresos_mes_usd''').fetchone()
        alertas_stock = conn.execute('SELECT nombre,codigo,stock_actual,stock_minimo FROM productos WHERE stock_actual<=stock_minimo ORDER BY stock_actual ASC LIMIT 5').fetchall()
        ventas_por_dia = conn.execute("SELECT date(creado_en) as dia, COUNT(*) as cantidad, SUM(total_usd) as total FROM ventas WHERE strftime('%Y-%m',creado_en)=strftime('%Y-%m','now') GROUP BY dia ORDER BY dia").fetchall()
        ventas_categoria = conn.execute('SELECT p.categoria, COUNT(*) as cantidad, SUM(v.total_usd) as total FROM ventas v JOIN productos p ON v.producto_id=p.id GROUP BY p.categoria ORDER BY cantidad DESC').fetchall()
        meses_anteriores = conn.execute("SELECT strftime('%Y-%m',creado_en) as mes, COUNT(*) as cantidad, SUM(total_usd) as total_usd, SUM(total_oficial) as total_oficial FROM ventas WHERE strftime('%Y-%m',creado_en)<strftime('%Y-%m','now') GROUP BY mes ORDER BY mes DESC LIMIT 6").fetchall()
        conn.close()
    except Exception as e: logging.error(f"Error dashboard: {e}"); flash('Error cargando dashboard','warning')
    return render_template('user/dashboard.html', suscripcion=suscripcion, stats=stats, alertas_stock=alertas_stock, ventas_por_dia=ventas_por_dia, ventas_categoria=ventas_categoria, meses_anteriores=meses_anteriores, tasas=tasas, limites=limites, tasa_bcv=tasa_bcv, config_empresa=config_empresa)

@app.route('/user/inventario')
@login_required
def user_inventario():
    user_id = session['user_id']
    search = request.args.get('search','').strip(); categoria = request.args.get('categoria','').strip()
    subcategoria = request.args.get('subcategoria','').strip(); marca = request.args.get('marca','').strip()
    productos = []; categorias_db = []; subcategorias_db = []
    tasas = obtener_tasas_usuario(user_id); stats = {'total':0,'stock_total':0,'valor_total':0}
    try:
        init_user_db(user_id)
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        query = 'SELECT * FROM productos WHERE 1=1'; params = []
        if search: query += ' AND (nombre LIKE ? OR codigo LIKE ? OR referencia LIKE ?)'; params.extend([f'%{search}%']*3)
        if categoria: query += ' AND categoria=?'; params.append(categoria)
        if subcategoria: query += ' AND subcategoria=?'; params.append(subcategoria)
        if marca: query += ' AND marca=?'; params.append(marca)
        query += ' ORDER BY nombre'
        productos_raw = conn.execute(query, params).fetchall()
        categorias_db = conn.execute("SELECT DISTINCT categoria FROM productos WHERE categoria IS NOT NULL AND categoria!='' ORDER BY categoria").fetchall()
        if categoria: subcategorias_db = conn.execute("SELECT DISTINCT subcategoria FROM productos WHERE categoria=? AND subcategoria IS NOT NULL AND subcategoria!='' ORDER BY subcategoria",(categoria,)).fetchall()
        sr = conn.execute('SELECT COALESCE(COUNT(*),0) as total, COALESCE(SUM(stock_actual),0) as stock_total, COALESCE(SUM(precio_venta_usd*stock_actual),0) as valor_total FROM productos').fetchone()
        if sr: stats = {'total':sr['total']or 0,'stock_total':sr['stock_total']or 0,'valor_total':sr['valor_total']or 0}
        conn.close()
        productos = []
        for prod in productos_raw:
            pd = dict(prod)
            precios = calcular_precios_todas_tasas(pd.get('precio_venta_usd',0)or 0, tasas)
            imagen_nombre = pd.get('imagen_principal') or obtener_imagen_producto(user_id, pd['id'])
            imagen_url = f"/uploads/{user_id}/{imagen_nombre}" if imagen_nombre else None
            productos.append({**pd, 'precios':precios, 'imagen_url':imagen_url})
    except Exception as e: logging.error(f"Error inventario: {e}"); flash('Error cargando inventario','warning')
    return render_template('user/inventario.html', productos=productos, categorias_db=categorias_db, categorias_predefinidas=obtener_categorias_predefinidas(), subcategorias_db=subcategorias_db, subcategorias_moto=obtener_subcategorias_por_categoria('Repuestos Moto'), marcas_moto=obtener_marcas_moto(), search=search, categoria=categoria, subcategoria=subcategoria, marca=marca, tasas=tasas, stats=stats)

@app.route('/user/agregar_producto', methods=['POST'])
@login_required
def agregar_producto():
    user_id = session['user_id']
    limites = verificar_limites_usuario(user_id); cantidad_actual = cantidad_productos_usuario(user_id)
    if cantidad_actual >= limites['limite_productos']:
        flash(f'Limite de {limites["limite_productos"]} productos alcanzado. Adquiere VIP para ilimitado.','warning'); return redirect(url_for('user_inventario'))
    try:
        nombre = request.form.get('nombre','').strip(); codigo = request.form.get('codigo','').strip()
        categoria = request.form.get('categoria','').strip()
        if not nombre or not codigo or not categoria: flash('Nombre, codigo y categoria obligatorios','danger'); return redirect(url_for('user_inventario'))
        pc = float(request.form.get('precio_costo_usd',0)); pv = float(request.form.get('precio_venta_usd',0))
        sa = int(request.form.get('stock_actual',0)); sm = int(request.form.get('stock_minimo',5))
        if pv <= 0: flash('Precio de venta mayor a 0','danger'); return redirect(url_for('user_inventario'))
        mg = ((pv-pc)/pc*100) if pc > 0 else 0
        init_user_db(user_id)
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        if conn.execute('SELECT id FROM productos WHERE codigo=?',(codigo,)).fetchone(): flash('Codigo ya existe','danger'); conn.close(); return redirect(url_for('user_inventario'))
        cursor = conn.execute('''INSERT INTO productos (nombre,codigo,categoria,subcategoria,marca,modelo,referencia,color,descripcion,precio_costo_usd,precio_venta_usd,margen_ganancia,stock_actual,stock_minimo,ubicacion,fecha_expiracion,impuesto,proveedor_nombre,proveedor_contacto,proveedor_notas,pagado_contado,dias_credito,observaciones,medida_unidad,medida_cantidad) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (nombre,codigo,categoria,request.form.get('subcategoria','').strip(),request.form.get('marca','').strip(),request.form.get('modelo','').strip(),request.form.get('referencia','').strip(),request.form.get('color','').strip(),request.form.get('descripcion','').strip(),pc,pv,mg,sa,sm,request.form.get('ubicacion','').strip(),request.form.get('fecha_expiracion')or None,float(request.form.get('impuesto',16)),request.form.get('proveedor_nombre','').strip(),request.form.get('proveedor_contacto','').strip(),request.form.get('proveedor_notas','').strip(),request.form.get('pagado_contado')=='1',int(request.form.get('dias_credito',0)),request.form.get('observaciones','').strip(),request.form.get('medida_unidad','Unidad').strip(),float(request.form.get('medida_cantidad',1))))
        pid = cursor.lastrowid
        if 'imagen_producto' in request.files:
            img = request.files['imagen_producto']
            if img and img.filename:
                iname = guardar_imagen_producto(img, user_id, pid)
                if iname: conn.execute('UPDATE productos SET imagen_principal=? WHERE id=?',(iname,pid))
        conn.commit(); conn.close()
        flash('Producto agregado','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error al agregar producto','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/editar_producto/<int:producto_id>', methods=['POST'])
@login_required
def editar_producto(producto_id):
    user_id = session['user_id']
    try:
        nombre = request.form.get('nombre','').strip(); categoria = request.form.get('categoria','').strip()
        if not nombre or not categoria: flash('Nombre y categoria obligatorios','danger'); return redirect(url_for('user_inventario'))
        pc = float(request.form.get('precio_costo_usd',0)); pv = float(request.form.get('precio_venta_usd',0))
        sa = int(request.form.get('stock_actual',0)); sm = int(request.form.get('stock_minimo',5))
        if pv <= 0: flash('Precio mayor a 0','danger'); return redirect(url_for('user_inventario'))
        mg = ((pv-pc)/pc*100) if pc > 0 else 0
        init_user_db(user_id)
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20)
        if 'imagen_producto' in request.files:
            img = request.files['imagen_producto']
            if img and img.filename:
                eliminar_imagenes_producto(user_id, producto_id)
                iname = guardar_imagen_producto(img, user_id, producto_id)
                if iname: conn.execute('UPDATE productos SET imagen_principal=? WHERE id=?',(iname,producto_id))
        cursor = conn.execute('''UPDATE productos SET nombre=?,categoria=?,subcategoria=?,marca=?,modelo=?,referencia=?,color=?,descripcion=?,precio_costo_usd=?,precio_venta_usd=?,margen_ganancia=?,stock_actual=?,stock_minimo=?,ubicacion=?,fecha_expiracion=?,impuesto=?,observaciones=?,medida_unidad=?,medida_cantidad=?,actualizado_en=CURRENT_TIMESTAMP WHERE id=?''',
            (nombre,categoria,request.form.get('subcategoria','').strip(),request.form.get('marca','').strip(),request.form.get('modelo','').strip(),request.form.get('referencia','').strip(),request.form.get('color','').strip(),request.form.get('descripcion','').strip(),pc,pv,mg,sa,sm,request.form.get('ubicacion','').strip(),request.form.get('fecha_expiracion')or None,float(request.form.get('impuesto',16)),request.form.get('observaciones','').strip(),request.form.get('medida_unidad','Unidad').strip(),float(request.form.get('medida_cantidad',1)),producto_id))
        if cursor.rowcount > 0: conn.commit(); flash('Producto actualizado','success')
        else: flash('Producto no encontrado','danger')
        conn.close()
    except Exception as e: logging.error(f"Error: {e}"); flash('Error al actualizar','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/eliminar_producto/<int:producto_id>', methods=['POST'])
@login_required
def eliminar_producto(producto_id):
    user_id = session['user_id']
    try:
        init_user_db(user_id); db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20)
        if conn.execute('SELECT COUNT(*) as count FROM ventas WHERE producto_id=?',(producto_id,)).fetchone()['count']>0:
            flash('Tiene ventas asociadas','danger'); conn.close(); return redirect(url_for('user_inventario'))
        conn.execute('DELETE FROM productos WHERE id=?',(producto_id,)); conn.commit(); conn.close()
        eliminar_imagenes_producto(user_id, producto_id)
        flash('Producto eliminado','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/ajustar_stock/<int:producto_id>', methods=['POST'])
@login_required
def ajustar_stock(producto_id):
    user_id = session['user_id']
    try:
        ta = request.form.get('tipo_ajuste','').strip(); cant = int(request.form.get('cantidad',0))
        if cant <= 0 or ta not in ['entrada','salida']: flash('Datos invalidos','danger'); return redirect(url_for('user_inventario'))
        init_user_db(user_id); db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        p = conn.execute('SELECT nombre,stock_actual FROM productos WHERE id=?',(producto_id,)).fetchone()
        if not p: flash('Producto no encontrado','danger'); conn.close(); return redirect(url_for('user_inventario'))
        ns = p['stock_actual']+cant if ta=='entrada' else p['stock_actual']-cant
        if ns < 0: flash(f'Stock insuficiente. Disponible: {p["stock_actual"]}','danger'); conn.close(); return redirect(url_for('user_inventario'))
        conn.execute('UPDATE productos SET stock_actual=?, actualizado_en=CURRENT_TIMESTAMP WHERE id=?',(ns,producto_id))
        conn.commit(); conn.close()
        flash(f'Stock ajustado. Nuevo: {ns}','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('user_inventario'))

@app.route('/user/ventas')
@login_required
def user_ventas():
    user_id = session['user_id']; init_user_db(user_id)
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        pr = conn.execute('SELECT id,nombre,codigo,precio_venta_usd,stock_actual,categoria,marca,imagen_principal,medida_unidad,medida_cantidad FROM productos WHERE stock_actual>0 ORDER BY nombre').fetchall()
        vr = conn.execute('SELECT v.*, p.nombre as producto_nombre, p.codigo as producto_codigo FROM ventas v JOIN productos p ON v.producto_id=p.id ORDER BY v.creado_en DESC LIMIT 10').fetchall()
        stats = conn.execute('SELECT COUNT(*) as total_ventas, COALESCE(SUM(total_usd),0) as ingresos_usd FROM ventas').fetchone()
        sm = conn.execute("SELECT COUNT(*) as ventas_mes, COALESCE(SUM(total_usd),0) as ingresos_mes_usd FROM ventas WHERE strftime('%Y-%m',creado_en)=strftime('%Y-%m','now')").fetchone()
        conn.close()
        cm = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); cm.row_factory = sqlite3.Row
        vendedores = cm.execute('SELECT id,nombre,email FROM usuarios WHERE usuario_padre_id=? AND es_vendedor=1 AND activo=1 ORDER BY nombre',(user_id,)).fetchall(); cm.close()
        tasas = obtener_tasas_usuario(user_id)
        productos = []
        for prod in pr:
            pd = dict(prod); precios = calcular_precios_todas_tasas(pd['precio_venta_usd'], tasas)
            iname = pd.get('imagen_principal') or obtener_imagen_producto(user_id, pd['id'])
            iurl = f"/uploads/{user_id}/{iname}" if iname else None
            productos.append({**pd, 'precios':precios, 'imagen_url':iurl})
        return render_template('user/ventas.html', productos=productos, vendedores=vendedores, ventas_recientes=vr, stats=stats, stats_mes=sm, tasas=tasas)
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return render_template('user/ventas.html', productos=[], vendedores=[])

@app.route('/user/procesar_venta', methods=['POST'])
@login_required
def procesar_venta():
    user_id = session['user_id']
    limites = verificar_limites_usuario(user_id)
    if limites['limite_ventas'] > 0:
        vm = ventas_mensuales_usuario(user_id)
        if vm >= limites['limite_ventas']:
            return jsonify({'success':False, 'error':f'Limite de {limites["limite_ventas"]} ventas mensuales alcanzado. Adquiere VIP para ilimitado.'})
    data = request.get_json()
    if not data or 'items' not in data: return jsonify({'success':False, 'error':'Datos invalidos'})
    init_user_db(user_id)
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        tasas = obtener_tasas_usuario(user_id); ventas_procesadas = []; total_venta_usd = 0; venta_id = None
        for item in data['items']:
            pid = item['producto_id']; cant = int(item['cantidad']); pu = float(item['precio_usd'])
            tu = item.get('tasa_usada', tasas['activa'])
            p = conn.execute('SELECT nombre,stock_actual FROM productos WHERE id=?',(pid,)).fetchone()
            if not p: conn.close(); return jsonify({'success':False, 'error':f'Producto {pid} no encontrado'})
            if p['stock_actual'] < cant: conn.close(); return jsonify({'success':False, 'error':f'Stock insuficiente'})
            total = cant*pu; total_venta_usd += total
            conn.execute('UPDATE productos SET stock_actual=stock_actual-? WHERE id=?',(cant,pid))
            cursor = conn.execute('''INSERT INTO ventas (producto_id,vendedor_id,cantidad,precio_usd,tasa_oficial,tasa_manual1,tasa_manual2,tasa_usada,total_usd,total_oficial,total_manual1,total_manual2,metodo_pago,observaciones) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (pid, data.get('vendedor_id'), cant, pu, tasas['oficial'], tasas['manual1'], tasas['manual2'], tu, total, total*tasas['oficial'], total*tasas['manual1'], total*tasas['manual2'], data.get('metodo_pago','efectivo'), data.get('observaciones','')))
            if venta_id is None: venta_id = cursor.lastrowid
            ventas_procesadas.append({'producto':p['nombre'],'cantidad':cant,'precio_usd':pu,'total_usd':total,'nombre':p['nombre']})
        vn = session.get('user_name','Sistema')
        fp, fn, nf = generar_factura_venta(venta_id, ventas_procesadas, tasas, user_id, vn, data.get('metodo_pago','Efectivo'), data.get('observaciones',''))
        conn.execute('UPDATE ventas SET nro_factura=? WHERE id=?',(nf, venta_id))
        conn.commit(); conn.close()
        return jsonify({'success':True, 'message':f'Venta procesada', 'ventas':ventas_procesadas, 'total_venta_usd':total_venta_usd, 'factura_url':f'/ver_factura/{fn}', 'nro_factura':nf})
    except Exception as e: logging.error(f"Error: {e}"); return jsonify({'success':False, 'error':str(e)})

@app.route('/user/reportes')
@login_required
def user_reportes():
    user_id = session['user_id']; init_user_db(user_id)
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{user_id}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        vpm = conn.execute("SELECT strftime('%Y-%m',creado_en) as mes, COUNT(*) as cantidad, SUM(total_usd) as total_usd, SUM(total_oficial) as total_oficial FROM ventas GROUP BY mes ORDER BY mes DESC LIMIT 12").fetchall()
        tp = conn.execute('SELECT p.nombre, p.codigo, SUM(v.cantidad) as total_vendido, SUM(v.total_usd) as ingresos FROM ventas v JOIN productos p ON v.producto_id=p.id GROUP BY p.id ORDER BY total_vendido DESC LIMIT 10').fetchall()
        vc = conn.execute('SELECT p.categoria, COUNT(*) as cantidad, SUM(v.total_usd) as total_usd FROM ventas v JOIN productos p ON v.producto_id=p.id GROUP BY p.categoria ORDER BY cantidad DESC').fetchall()
        stats = conn.execute('SELECT COUNT(*) as total_productos, SUM(stock_actual) as stock_total, (SELECT COUNT(*) FROM ventas) as total_ventas, (SELECT COALESCE(SUM(total_usd),0) FROM ventas) as ingresos_totales FROM productos').fetchone()
        conn.close()
        return render_template('user/reportes.html', ventas_por_mes=vpm, top_productos=tp, ventas_categoria=vc, stats=stats)
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return render_template('user/reportes.html')

@app.route('/ver_factura/<filename>')
@login_required
def ver_factura(filename): return send_from_directory('facturas', filename)

@app.route('/user/facturas')
@login_required
def listar_facturas():
    user_id = session['user_id']; facturas_dir = 'facturas'; facturas = []
    if os.path.exists(facturas_dir):
        for file in sorted(os.listdir(facturas_dir), reverse=True):
            if file.endswith('.html') and f'_{user_id}.html' in file:
                partes = file.replace('factura_','').replace('.html','').split('-')
                facturas.append({'filename':file,'url':f'/ver_factura/{file}','nro':f"F-{partes[0]}" if partes else file,'fecha':f"{partes[0][:4]}-{partes[0][4:6]}-{partes[0][6:8]}" if len(partes[0])>=14 else 'N/A'})
    return render_template('user/facturas.html', facturas=facturas[:50])

# ============ RUTAS DE ADMINISTRADOR ============
@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        tu = conn.execute("SELECT COUNT(*) as count FROM usuarios WHERE role='user'").fetchone()['count']
        tv = conn.execute("SELECT COUNT(*) as count FROM usuarios WHERE es_vendedor=1").fetchone()['count']
        sa = conn.execute("SELECT COUNT(*) as count FROM suscripciones WHERE estado='activa' AND fecha_fin>=date('now')").fetchone()['count']
        spv = conn.execute("SELECT COUNT(*) as count FROM suscripciones WHERE estado='activa' AND fecha_fin BETWEEN date('now') AND date('now','+7 days')").fetchone()['count']
        ur = conn.execute("SELECT u.*, s.fecha_fin FROM usuarios u LEFT JOIN suscripciones s ON u.id=s.usuario_id AND s.estado='activa' WHERE u.role='user' ORDER BY u.fecha_registro DESC LIMIT 5").fetchall()
        conn.close()
        return render_template('admin/dashboard.html', total_usuarios=tu, total_vendedores=tv, suscripciones_activas=sa, suscripciones_por_vencer=spv, usuarios_recientes=ur)
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return render_template('admin/dashboard.html')

@app.route('/admin/usuarios')
@login_required
@admin_required
def admin_usuarios():
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        usuarios = conn.execute("SELECT u.*, s.fecha_fin, s.estado as estado_suscripcion, c.nombre as empresa_nombre, c.rif as empresa_rif FROM usuarios u LEFT JOIN suscripciones s ON u.id=s.usuario_id AND s.estado='activa' LEFT JOIN config_empresa c ON u.id=c.usuario_id WHERE u.role='user' ORDER BY u.nombre").fetchall()
        conn.close()
        return render_template('admin/usuarios.html', usuarios=usuarios)
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return render_template('admin/usuarios.html', usuarios=[])

@app.route('/admin/suscripciones')
@login_required
@admin_required
def admin_suscripciones():
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        suscripciones = conn.execute('SELECT s.*, u.nombre as usuario_nombre, u.email as usuario_email FROM suscripciones s JOIN usuarios u ON s.usuario_id=u.id ORDER BY s.creado_en DESC').fetchall()
        usuarios_sin = conn.execute("SELECT id, nombre, email FROM usuarios WHERE role='user' AND (suscripcion_activa=0 OR suscripcion_activa IS NULL OR fecha_fin_suscripcion<date('now')) ORDER BY nombre").fetchall()
        conn.close()
        return render_template('admin/suscripciones.html', suscripciones=[dict(s) for s in suscripciones], usuarios_sin_suscripcion=[dict(u) for u in usuarios_sin])
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return render_template('admin/suscripciones.html', suscripciones=[], usuarios_sin_suscripcion=[])

@app.route('/admin/crear_suscripcion', methods=['POST'])
@login_required
@admin_required
def crear_suscripcion():
    try:
        uid = request.form.get('usuario_id'); plan = request.form.get('plan')
        dias = int(request.form.get('dias',30)); monto = float(request.form.get('monto',0))
        if not uid or not plan: flash('Usuario y plan requeridos','danger'); return redirect(url_for('admin_suscripciones'))
        fi = date.today(); ff = fi + timedelta(days=dias)
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20)
        if conn.execute('SELECT id FROM suscripciones WHERE usuario_id=? AND estado="activa" AND fecha_fin>=date("now")',(uid,)).fetchone():
            flash('Ya tiene suscripcion activa','warning'); conn.close(); return redirect(url_for('admin_suscripciones'))
        conn.execute('INSERT INTO suscripciones (usuario_id,plan,dias,fecha_inicio,fecha_fin,monto,metodo_pago,notas,creado_por) VALUES (?,?,?,?,?,?,?,?,?)',
                   (uid, plan, dias, fi, ff, monto, 'admin', request.form.get('notas',''), session['user_id']))
        conn.execute('UPDATE usuarios SET suscripcion_activa=1, fecha_fin_suscripcion=?, limite_productos=?, limite_ventas_mensuales=? WHERE id=?',
                   (ff, obtener_limite_productos_por_plan(plan), obtener_limite_ventas_por_plan(plan), uid))
        conn.commit(); conn.close()
        flash(f'Suscripcion VIP creada. Vence: {ff}','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('admin_suscripciones'))

@app.route('/admin/cancelar_suscripcion/<int:suscripcion_id>', methods=['POST'])
@login_required
@admin_required
def cancelar_suscripcion(suscripcion_id):
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        s = conn.execute('SELECT * FROM suscripciones WHERE id=?',(suscripcion_id,)).fetchone()
        if not s: flash('No encontrada','danger'); conn.close(); return redirect(url_for('admin_suscripciones'))
        sd = dict(s)
        conn.execute('UPDATE suscripciones SET estado="cancelada", cancelado_en=date("now"), cancelado_por=? WHERE id=?',(session['user_id'],suscripcion_id))
        conn.execute('UPDATE usuarios SET suscripcion_activa=0, limite_productos=50, limite_ventas_mensuales=20 WHERE id=?',(sd['usuario_id'],))
        conn.commit(); conn.close()
        flash('Suscripcion cancelada. Usuario vuelve a plan Free.','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('admin_suscripciones'))

@app.route('/admin/extender_suscripcion/<int:suscripcion_id>', methods=['POST'])
@login_required
@admin_required
def extender_suscripcion(suscripcion_id):
    try:
        de = int(request.form.get('dias_extra',30))
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        s = conn.execute('SELECT * FROM suscripciones WHERE id=?',(suscripcion_id,)).fetchone()
        if not s: flash('No encontrada','danger'); conn.close(); return redirect(url_for('admin_suscripciones'))
        sd = dict(s)
        nf = datetime.strptime(sd['fecha_fin'],'%Y-%m-%d') + timedelta(days=de)
        conn.execute('UPDATE suscripciones SET fecha_fin=?, dias=dias+? WHERE id=?',(nf.strftime('%Y-%m-%d'), de, suscripcion_id))
        conn.execute('UPDATE usuarios SET fecha_fin_suscripcion=? WHERE id=?',(nf.strftime('%Y-%m-%d'), sd['usuario_id']))
        conn.commit(); conn.close()
        flash(f'Extendida {de} dias. Nueva fecha: {nf.strftime("%d/%m/%Y")}','success')
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return redirect(url_for('admin_suscripciones'))

@app.route('/admin/configurar_empresa/<int:usuario_id>', methods=['GET','POST'])
@login_required
@admin_required
def admin_configurar_empresa(usuario_id):
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PRINCIPAL, timeout=20); conn.row_factory = sqlite3.Row
        usuario = conn.execute('SELECT id,nombre,email FROM usuarios WHERE id=?',(usuario_id,)).fetchone()
        if not usuario: flash('Usuario no encontrado','danger'); return redirect(url_for('admin_usuarios'))
        if request.method == 'POST':
            datos = {'nombre':request.form.get('nombre','').strip(),'rif':request.form.get('rif','').strip(),'direccion':request.form.get('direccion','').strip(),'telefono':request.form.get('telefono','').strip(),'email':request.form.get('email','').strip(),'mensaje_factura':request.form.get('mensaje_factura','').strip()}
            actualizar_config_empresa(usuario_id, datos)
            flash('Configuracion actualizada','success')
            return redirect(url_for('admin_configurar_empresa', usuario_id=usuario_id))
        config = obtener_config_empresa(usuario_id); conn.close()
        return render_template('admin/configurar_empresa.html', usuario=dict(usuario), config=config)
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    if conn: conn.close()
    return redirect(url_for('admin_usuarios'))

# ============ RUTAS DE VENDEDOR ============
@app.route('/vendedor/ventas')
@login_required
@vendedor_required
def vendedor_ventas():
    vid = session['user_id']; upid = session.get('usuario_padre_id')
    if not upid: flash('Sin usuario padre','danger'); return redirect(url_for('logout'))
    init_user_db(upid)
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{upid}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        pr = conn.execute('SELECT id,nombre,codigo,precio_venta_usd,stock_actual,categoria,imagen_principal,medida_unidad,medida_cantidad FROM productos WHERE stock_actual>0 ORDER BY nombre').fetchall()
        vr = conn.execute('SELECT v.*, p.nombre as producto_nombre FROM ventas v JOIN productos p ON v.producto_id=p.id WHERE v.vendedor_id=? ORDER BY v.creado_en DESC LIMIT 10',(vid,)).fetchall()
        stats = conn.execute('SELECT COUNT(*) as total, COALESCE(SUM(total_usd),0) as ingresos FROM ventas WHERE vendedor_id=?',(vid,)).fetchone()
        conn.close()
        tasas = obtener_tasas_usuario(upid)
        productos = []
        for prod in pr:
            pd = dict(prod); precios = calcular_precios_todas_tasas(pd['precio_venta_usd'], tasas)
            iname = pd.get('imagen_principal') or obtener_imagen_producto(upid, pd['id'])
            iurl = f"/uploads/{upid}/{iname}" if iname else None
            productos.append({**pd, 'precios':precios, 'imagen_url':iurl})
        return render_template('vendedor/ventas.html', productos=productos, ventas_recientes=vr, stats=stats, tasas=tasas)
    except Exception as e: logging.error(f"Error: {e}"); flash('Error','danger')
    return render_template('vendedor/ventas.html', productos=[])

@app.route('/vendedor/procesar_venta', methods=['POST'])
@login_required
@vendedor_required
def vendedor_procesar_venta():
    vid = session['user_id']; upid = session.get('usuario_padre_id')
    if not upid: return jsonify({'success':False, 'error':'Sin usuario padre'})
    data = request.get_json()
    if not data or 'items' not in data: return jsonify({'success':False, 'error':'Datos invalidos'})
    init_user_db(upid)
    try:
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{upid}.db')
        conn = sqlite3.connect(db_path, timeout=20); conn.row_factory = sqlite3.Row
        tasas = obtener_tasas_usuario(upid); ventas_procesadas = []; venta_id = None
        for item in data['items']:
            pid = item['producto_id']; cant = int(item['cantidad']); pu = float(item['precio_usd'])
            p = conn.execute('SELECT nombre,stock_actual FROM productos WHERE id=?',(pid,)).fetchone()
            if not p or p['stock_actual']<cant: conn.close(); return jsonify({'success':False, 'error':'Stock insuficiente'})
            total = cant*pu
            conn.execute('UPDATE productos SET stock_actual=stock_actual-? WHERE id=?',(cant,pid))
            cursor = conn.execute('INSERT INTO ventas (producto_id,vendedor_id,cantidad,precio_usd,tasa_oficial,tasa_manual1,tasa_manual2,tasa_usada,total_usd,total_oficial,total_manual1,total_manual2,metodo_pago,observaciones) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (pid,vid,cant,pu,tasas['oficial'],tasas['manual1'],tasas['manual2'],item.get('tasa_usada',tasas['activa']),total,total*tasas['oficial'],total*tasas['manual1'],total*tasas['manual2'],'efectivo',f'Vendedor {vid}'))
            if venta_id is None: venta_id = cursor.lastrowid
            ventas_procesadas.append({'producto':p['nombre'],'cantidad':cant,'precio_usd':pu,'total_usd':total,'nombre':p['nombre']})
        vn = session.get('user_name','Vendedor')
        fp, fn, nf = generar_factura_venta(venta_id, ventas_procesadas, tasas, upid, vn, 'Efectivo', '')
        conn.execute('UPDATE ventas SET nro_factura=? WHERE id=?',(nf,venta_id))
        conn.commit(); conn.close()
        return jsonify({'success':True, 'message':'Venta procesada', 'ventas':ventas_procesadas, 'factura_url':f'/ver_factura/{fn}', 'nro_factura':nf})
    except Exception as e: logging.error(f"Error: {e}"); return jsonify({'success':False, 'error':str(e)})

# ============ API TASAS ============
@app.route('/api/tasa_bcv')
def api_tasa_bcv():
    try: return jsonify({'success':True, 'tasa':obtener_tasa_bcv()})
    except: return jsonify({'success':False, 'tasa':36})

@app.route('/user/actualizar_tasa_oficial', methods=['POST'])
@login_required
def actualizar_tasa_oficial():
    uid = session['user_id']
    try:
        tasa = obtener_tasa_bcv(); init_user_db(uid)
        db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        conn = sqlite3.connect(db_path, timeout=20)
        conn.execute('INSERT INTO historial_tasas (tasa_oficial,cambio_por) VALUES (?,?)',(tasa,'BCV'))
        conn.execute("UPDATE config_tasa SET tasa_oficial=?, tasa_activa='oficial', fuente_oficial='BCV', actualizado_en=CURRENT_TIMESTAMP",(tasa,))
        conn.commit(); conn.close(); session['tasa_activa']='oficial'
        return jsonify({'success':True, 'tasa':tasa})
    except Exception as e: return jsonify({'success':False, 'error':str(e)})

@app.route('/user/configurar_tasas', methods=['POST'])
@login_required
def configurar_tasas():
    uid = session['user_id']; data = request.get_json()
    if not data.get('tasa_manual1') or not data.get('tasa_manual2'): return jsonify({'success':False})
    try:
        init_user_db(uid); db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
        conn = sqlite3.connect(db_path, timeout=20)
        conn.execute('INSERT INTO historial_tasas (tasa_manual1,tasa_manual2,tasa_activa,cambio_por) VALUES (?,?,?,?)',(data['tasa_manual1'],data['tasa_manual2'],data.get('tasa_activa','oficial'),'usuario'))
        conn.execute('UPDATE config_tasa SET tasa_manual1=?, tasa_manual2=?, tasa_activa=?, actualizado_en=CURRENT_TIMESTAMP',(float(data['tasa_manual1']),float(data['tasa_manual2']),data.get('tasa_activa','oficial')))
        conn.commit(); conn.close(); session['tasa_activa']=data.get('tasa_activa','oficial')
        return jsonify({'success':True})
    except Exception as e: return jsonify({'success':False, 'error':str(e)})

@app.route('/api/actualizar_tasa_ahora')
@login_required
def actualizar_tasa_ahora():
    try:
        cache_file = 'tasa_cache.json'
        if os.path.exists(cache_file): os.remove(cache_file)
        tasa = obtener_tasa_bcv(); uid = session['user_id']
        if uid and not session.get('es_vendedor'):
            init_user_db(uid); db_path = os.path.join(app.config['DATABASE_DIR'], f'user_{uid}.db')
            conn = sqlite3.connect(db_path, timeout=20)
            conn.execute('INSERT INTO historial_tasas (tasa_oficial,cambio_por) VALUES (?,?)',(tasa,'Manual'))
            conn.execute("UPDATE config_tasa SET tasa_oficial=?, fuente_oficial='BCV', actualizado_en=CURRENT_TIMESTAMP",(tasa,))
            conn.commit(); conn.close()
        return jsonify({'success':True, 'tasa':tasa})
    except Exception as e: return jsonify({'success':False, 'error':str(e)})

@app.route('/api/obtener_subcategorias')
def obtener_subcategorias():
    return jsonify({'success':True, 'subcategorias':obtener_subcategorias_por_categoria(request.args.get('categoria',''))})

@app.route('/uploads/<path:filename>')
def uploaded_files(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ============ ERROR HANDLERS ============
@app.errorhandler(404)
def not_found(error): return render_template('error.html', error='Pagina no encontrada', codigo=404), 404
@app.errorhandler(500)
def internal_error(error): logging.error(f"Error 500: {error}"); return render_template('error.html', error='Error interno', codigo=500), 500
@app.errorhandler(403)
def forbidden(error): return render_template('error.html', error='Acceso denegado', codigo=403), 403

# ============ INICIALIZACION ============
if __name__ == '__main__':
    with app.app_context(): init_main_db(); logging.info("Aplicacion inicializada")
    if not app.debug or os.environ.get('WERKZEUG_RUN_MAIN')=='true':
        scheduler = BackgroundScheduler()
        scheduler.add_job(func=lambda:[obtener_tasa_bcv()], trigger="interval", hours=1)
        scheduler.start(); atexit.register(lambda: scheduler.shutdown())
    app.run(debug=os.environ.get('FLASK_ENV')=='development', host='0.0.0.0', port=int(os.environ.get('PORT',5000)), threaded=True)
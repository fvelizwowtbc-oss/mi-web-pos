// Utilidades generales para la aplicación

document.addEventListener('DOMContentLoaded', function() {
    // Inicializar tooltips
    initTooltips();
    
    // Configurar forms
    initForms();
    
    // Configurar tablas
    initTables();
    
    // Inicializar modales
    initModals();
    
    // Inicializar componentes específicos si existen
    initVentasComponent();
    initInventarioComponent();
});

function initTooltips() {
    const tooltips = document.querySelectorAll('[title]');
    tooltips.forEach(tooltip => {
        tooltip.addEventListener('mouseenter', function() {
            const title = this.getAttribute('title');
            if (title) {
                const tooltip = document.createElement('div');
                tooltip.className = 'custom-tooltip';
                tooltip.textContent = title;
                document.body.appendChild(tooltip);
                
                const rect = this.getBoundingClientRect();
                tooltip.style.position = 'fixed';
                tooltip.style.left = rect.left + (rect.width / 2) + 'px';
                tooltip.style.top = rect.top - 40 + 'px';
                tooltip.style.transform = 'translateX(-50%)';
                
                this.removeAttribute('title');
                
                this.addEventListener('mouseleave', function() {
                    tooltip.remove();
                    this.setAttribute('title', title);
                }, { once: true });
            }
        });
    });
}

function initForms() {
    // Validación de formularios
    const forms = document.querySelectorAll('form');
    forms.forEach(form => {
        form.addEventListener('submit', function(e) {
            const required = form.querySelectorAll('[required]');
            let valid = true;
            
            required.forEach(field => {
                if (!field.value.trim()) {
                    valid = false;
                    field.classList.add('is-invalid');
                    field.style.borderColor = '#dc3545';
                } else {
                    field.classList.remove('is-invalid');
                    field.style.borderColor = '';
                }
            });
            
            if (!valid) {
                e.preventDefault();
                showNotification('Por favor, completa todos los campos requeridos.', 'danger');
            }
        });
    });
}

function initTables() {
    // Agregar funcionalidad a las tablas
    const tables = document.querySelectorAll('.data-table');
    tables.forEach(table => {
        // Agregar ordenamiento si tiene th clickeables
        const headers = table.querySelectorAll('th[data-sort]');
        headers.forEach(header => {
            header.style.cursor = 'pointer';
            header.addEventListener('click', function() {
                sortTable(table, this.cellIndex, this.getAttribute('data-sort'));
            });
        });
        
        // Agregar stripes a las filas
        const rows = table.querySelectorAll('tbody tr');
        rows.forEach((row, index) => {
            if (index % 2 === 0) {
                row.style.backgroundColor = '#f8f9fa';
            }
        });
        
        // Mejorar responsividad
        makeTableResponsive(table);
    });
}

function makeTableResponsive(table) {
    // Añadir clase para tablas responsivas
    if (!table.parentElement.classList.contains('table-responsive')) {
        const wrapper = document.createElement('div');
        wrapper.className = 'table-responsive';
        table.parentElement.insertBefore(wrapper, table);
        wrapper.appendChild(table);
    }
}

function initModals() {
    // Cerrar modales con tecla ESC
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            const modals = document.querySelectorAll('.modal');
            modals.forEach(modal => {
                if (modal.style.display === 'flex') {
                    modal.style.display = 'none';
                }
            });
        }
    });
    
    // Cerrar modal al hacer clic fuera
    document.addEventListener('click', function(e) {
        if (e.target.classList.contains('modal')) {
            e.target.style.display = 'none';
        }
    });
}

function initVentasComponent() {
    // Inicializar funcionalidad específica de ventas si existe
    const ventasContainer = document.querySelector('.ventas-container');
    if (ventasContainer) {
        console.log('Componente de ventas inicializado');
        
        // Aquí puedes agregar funcionalidad específica para ventas
        // Por ejemplo, inicializar el carrito si existe
        if (typeof window.actualizarCarrito === 'function') {
            window.actualizarCarrito();
        }
    }
}

function initInventarioComponent() {
    // Inicializar funcionalidad específica de inventario si existe
    const inventarioContainer = document.querySelector('.inventario-container');
    if (inventarioContainer) {
        console.log('Componente de inventario inicializado');
        
        // Inicializar filtros de inventario
        initInventarioFilters();
    }
}

function initInventarioFilters() {
    const filterForm = document.querySelector('.filter-form');
    if (filterForm) {
        filterForm.addEventListener('submit', function(e) {
            e.preventDefault();
            // Lógica de filtrado de inventario
            console.log('Filtrando inventario...');
        });
    }
}

function sortTable(table, column, type) {
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr'));
    
    // Determinar dirección de ordenamiento
    const currentSort = table.getAttribute('data-sort-dir') || 'asc';
    const newSort = currentSort === 'asc' ? 'desc' : 'asc';
    table.setAttribute('data-sort-dir', newSort);
    
    rows.sort((a, b) => {
        let aVal = a.cells[column].textContent.trim();
        let bVal = b.cells[column].textContent.trim();
        
        if (type === 'numeric') {
            aVal = parseFloat(aVal) || 0;
            bVal = parseFloat(bVal) || 0;
            return currentSort === 'asc' ? aVal - bVal : bVal - aVal;
        } else if (type === 'date') {
            aVal = new Date(aVal);
            bVal = new Date(bVal);
            return currentSort === 'asc' ? aVal - bVal : bVal - aVal;
        } else {
            return currentSort === 'asc' ? 
                aVal.localeCompare(bVal) : 
                bVal.localeCompare(aVal);
        }
    });
    
    // Limpiar y reinsertar filas ordenadas
    tbody.innerHTML = '';
    rows.forEach(row => tbody.appendChild(row));
    
    // Actualizar indicadores de ordenamiento
    const headers = table.querySelectorAll('th');
    headers.forEach(header => {
        header.classList.remove('sort-asc', 'sort-desc');
    });
    
    const currentHeader = headers[column];
    currentHeader.classList.add(`sort-${currentSort}`);
}

// Funciones para tasas
function formatCurrency(amount, currency = 'USD') {
    const formatter = new Intl.NumberFormat('es-VE', {
        style: 'currency',
        currency: currency === 'USD' ? 'USD' : 'VES',
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });
    
    return formatter.format(amount);
}

function calculatePriceWithRate(priceUSD, rate) {
    return parseFloat(priceUSD) * parseFloat(rate);
}

// Funciones para inventario
function updateStock(productId, delta) {
    // Aquí iría la lógica para actualizar stock via AJAX
    console.log(`Actualizando producto ${productId} con delta ${delta}`);
    
    return fetch(`/api/stock/${productId}`, {
        method: 'PATCH',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify({ delta: delta })
    })
    .then(response => response.json())
    .catch(error => {
        console.error('Error actualizando stock:', error);
        showNotification('Error al actualizar el stock', 'danger');
        throw error;
    });
}

// Funciones para ventas
function calculateCartTotal(cartItems, activeRate) {
    let totalUSD = 0;
    let totalVES = 0;
    
    cartItems.forEach(item => {
        totalUSD += item.priceUSD * item.quantity;
        totalVES += item.priceUSD * item.quantity * activeRate;
    });
    
    return {
        usd: totalUSD.toFixed(2),
        ves: totalVES.toFixed(2)
    };
}

// Funciones de utilidad
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

function showNotification(message, type = 'info') {
    // Crear contenedor si no existe
    let container = document.querySelector('.notification-container');
    if (!container) {
        container = document.createElement('div');
        container.className = 'notification-container';
        container.style.position = 'fixed';
        container.style.top = '20px';
        container.style.right = '20px';
        container.style.zIndex = '10000';
        document.body.appendChild(container);
    }
    
    // Crear notificación
    const notification = document.createElement('div');
    notification.className = `notification notification-${type}`;
    notification.innerHTML = `
        <div class="notification-content">
            ${message}
            <button class="notification-close">&times;</button>
        </div>
    `;
    
    container.appendChild(notification);
    
    // Estilos
    notification.style.backgroundColor = type === 'success' ? '#d4edda' : 
                                       type === 'danger' ? '#f8d7da' :
                                       type === 'warning' ? '#fff3cd' : '#d1ecf1';
    notification.style.color = type === 'success' ? '#155724' : 
                              type === 'danger' ? '#721c24' :
                              type === 'warning' ? '#856404' : '#0c5460';
    notification.style.padding = '15px 20px';
    notification.style.borderRadius = '6px';
    notification.style.marginBottom = '10px';
    notification.style.border = '1px solid';
    notification.style.borderColor = type === 'success' ? '#c3e6cb' : 
                                   type === 'danger' ? '#f5c6cb' :
                                   type === 'warning' ? '#ffeeba' : '#bee5eb';
    notification.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
    notification.style.transform = 'translateX(100%)';
    notification.style.transition = 'transform 0.3s ease';
    
    setTimeout(() => {
        notification.style.transform = 'translateX(0)';
    }, 10);
    
    // Botón para cerrar
    notification.querySelector('.notification-close').addEventListener('click', function() {
        notification.style.transform = 'translateX(100%)';
        setTimeout(() => {
            notification.remove();
        }, 300);
    });
    
    // Cerrar automáticamente después de 5 segundos
    setTimeout(() => {
        if (notification.parentNode) {
            notification.style.transform = 'translateX(100%)';
            setTimeout(() => {
                notification.remove();
            }, 300);
        }
    }, 5000);
}

// Función para cargar datos via AJAX
function fetchData(url, options = {}) {
    return fetch(url, {
        headers: {
            'Content-Type': 'application/json',
            ...options.headers
        },
        ...options
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return response.json();
    })
    .catch(error => {
        console.error('Error fetching data:', error);
        showNotification('Error al cargar los datos', 'danger');
        throw error;
    });
}

// Función para enviar datos via AJAX
function postData(url, data) {
    // Obtener token CSRF si existe
    const csrfToken = document.querySelector('meta[name="csrf-token"]')?.getAttribute('content');
    
    return fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(csrfToken && { 'X-CSRFToken': csrfToken })
        },
        body: JSON.stringify(data)
    })
    .then(response => {
        if (!response.ok) {
            throw new Error(`HTTP error! status: ${response.status}`);
        }
        return response.json();
    })
    .catch(error => {
        console.error('Error posting data:', error);
        showNotification('Error al enviar los datos', 'danger');
        throw error;
    });
}

// Función para formatear fechas
function formatDate(dateString) {
    try {
        const date = new Date(dateString);
        return date.toLocaleDateString('es-VE', {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (error) {
        return dateString || 'Fecha no disponible';
    }
}

// Función para formatear números
function formatNumber(number, decimals = 2) {
    const num = parseFloat(number);
    if (isNaN(num)) return '0.00';
    
    return num.toLocaleString('es-VE', {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals
    });
}

// Función para obtener el token CSRF
function getCSRFToken() {
    return document.querySelector('meta[name="csrf-token"]')?.getAttribute('content') || '';
}

// Función para inicializar búsquedas
function initSearch(inputSelector, itemsSelector, searchCallback) {
    const searchInput = document.querySelector(inputSelector);
    if (!searchInput) return;
    
    const debouncedSearch = debounce(searchCallback, 300);
    
    searchInput.addEventListener('input', function() {
        debouncedSearch(this.value);
    });
    
    // También buscar con Enter
    searchInput.addEventListener('keypress', function(e) {
        if (e.key === 'Enter') {
            e.preventDefault();
            searchCallback(this.value);
        }
    });
}

// Función para confirmar acciones
function confirmAction(message, callback) {
    if (confirm(message)) {
        callback();
    }
}

// Función para mostrar/ocultar loading
function showLoading(element) {
    if (!element) return;
    
    const loadingDiv = document.createElement('div');
    loadingDiv.className = 'loading-overlay';
    loadingDiv.innerHTML = '<div class="loading-spinner"></div>';
    
    loadingDiv.style.position = 'absolute';
    loadingDiv.style.top = '0';
    loadingDiv.style.left = '0';
    loadingDiv.style.width = '100%';
    loadingDiv.style.height = '100%';
    loadingDiv.style.backgroundColor = 'rgba(255, 255, 255, 0.8)';
    loadingDiv.style.display = 'flex';
    loadingDiv.style.alignItems = 'center';
    loadingDiv.style.justifyContent = 'center';
    loadingDiv.style.zIndex = '1000';
    
    element.style.position = 'relative';
    element.appendChild(loadingDiv);
    
    return loadingDiv;
}

function hideLoading(loadingDiv) {
    if (loadingDiv && loadingDiv.parentElement) {
        loadingDiv.parentElement.removeChild(loadingDiv);
    }
}

// Exportar funciones para uso global
window.appUtils = {
    formatCurrency,
    calculatePriceWithRate,
    calculateCartTotal,
    showNotification,
    debounce,
    fetchData,
    postData,
    formatDate,
    formatNumber,
    getCSRFToken,
    initSearch,
    confirmAction,
    showLoading,
    hideLoading,
    updateStock
};

// Estilos para tooltips y componentes
const appStyles = document.createElement('style');
appStyles.textContent = `
    .custom-tooltip {
        position: absolute;
        background: rgba(0, 0, 0, 0.8);
        color: white;
        padding: 6px 12px;
        border-radius: 4px;
        font-size: 0.875rem;
        z-index: 10000;
        pointer-events: none;
        white-space: nowrap;
    }
    
    .custom-tooltip:after {
        content: '';
        position: absolute;
        top: 100%;
        left: 50%;
        margin-left: -5px;
        border-width: 5px;
        border-style: solid;
        border-color: rgba(0, 0, 0, 0.8) transparent transparent transparent;
    }
    
    .notification-close {
        background: none;
        border: none;
        color: inherit;
        font-size: 1.2rem;
        cursor: pointer;
        margin-left: 10px;
        padding: 0;
        line-height: 1;
    }
    
    .loading-spinner {
        width: 40px;
        height: 40px;
        border: 4px solid #f3f3f3;
        border-top: 4px solid #3498db;
        border-radius: 50%;
        animation: spin 1s linear infinite;
    }
    
    @keyframes spin {
        0% { transform: rotate(0deg); }
        100% { transform: rotate(360deg); }
    }
    
    .sort-asc::after {
        content: ' ↑';
        font-weight: bold;
        color: #3498db;
    }
    
    .sort-desc::after {
        content: ' ↓';
        font-weight: bold;
        color: #3498db;
    }
    
    .is-invalid {
        border-color: #dc3545 !important;
        background-color: #fff8f8;
    }
    
    .is-invalid:focus {
        box-shadow: 0 0 0 0.2rem rgba(220, 53, 69, 0.25);
    }
`;
document.head.appendChild(appStyles);
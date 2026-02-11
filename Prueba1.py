import streamlit as st
import pandas as pd
import openseespy.opensees as ops
import matplotlib.pyplot as plt
import opsvis as opsv

# --- 1. CONFIGURACIÓN ---
st.set_page_config(page_title="Pórtico Paramétrico Final", layout="wide")
st.title("🏗️ Análisis de Pórtico: Secciones Rectangulares y Cargas")

# Estilos CSS
st.markdown("""
    <style>
    div.stButton > button:first-child {
        background-color: #28a745; color: white; border-radius: 8px; font-weight: bold;
    }
    </style>
""", unsafe_allow_html=True)

# --- 2. FUNCIÓN GENERADORA DE GEOMETRÍA ---
def generar_modelo_completo(n_pisos, h_piso, df_vanos, df_sismo):
    nodes = []
    fixes = []
    elems = []
    loads_dist = [] 
    loads_punt = [] 
    
    node_tag = 1
    ele_tag = 1
    
    # --- PROCESAR VANOS ---
    longitudes = df_vanos["Longitud (m)"].tolist()
    cargas_vigas = df_vanos["Carga Vert. (Ton/m)"].tolist()
    
    if len(longitudes) == 0: return pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    coords_x = [0.0]
    acumulado = 0.0
    for l in longitudes:
        acumulado += l
        coords_x.append(acumulado)
    
    num_lineas_col = len(coords_x)
    grid_nodes = {} 

    # --- A. NODOS Y RESTRICCIONES ---
    for piso in range(n_pisos + 1):
        y = piso * h_piso
        for i_col, x in enumerate(coords_x):
            nodes.append({"Node_ID": node_tag, "X": x, "Y": y})
            grid_nodes[(piso, i_col)] = node_tag
            
            if piso == 0: 
                fixes.append({"Node_ID": node_tag, "Ux": 1, "Uy": 1, "Uz": 1})
            
            node_tag += 1

    # --- B. ELEMENTOS ---
    for piso in range(n_pisos + 1):
        for i_col in range(num_lineas_col):
            current_node = grid_nodes[(piso, i_col)]
            
            # Columnas
            if piso < n_pisos:
                node_arriba = grid_nodes[(piso + 1, i_col)]
                elems.append({"Element_ID": ele_tag, "i": current_node, "j": node_arriba, "Seccion_ID": 1})
                ele_tag += 1
            
            # Vigas
            if i_col < (num_lineas_col - 1) and piso > 0:
                node_derecha = grid_nodes[(piso, i_col + 1)]
                elems.append({"Element_ID": ele_tag, "i": current_node, "j": node_derecha, "Seccion_ID": 2})
                
                w = cargas_vigas[i_col]
                if w != 0:
                    loads_dist.append({"Element_ID": ele_tag, "Wy": w, "Wx": 0.0})
                ele_tag += 1

    # --- C. CARGAS LATERALES ---
    if not df_sismo.empty:
        for index, row in df_sismo.iterrows():
            piso_objetivo = int(row["Piso N°"])
            fuerza_x = float(row["Fuerza X (Ton)"])
            
            if 0 < piso_objetivo <= n_pisos:
                nodo_impacto = grid_nodes[(piso_objetivo, 0)]
                loads_punt.append({
                    "Node_ID": nodo_impacto,
                    "Fx": fuerza_x, "Fy": 0.0, "Mz": 0.0
                })

    return pd.DataFrame(nodes), pd.DataFrame(fixes), pd.DataFrame(elems), pd.DataFrame(loads_punt), pd.DataFrame(loads_dist)

# --- 3. MOTOR DE CÁLCULO (SIN LIBRERÍA EXTERNA) ---
def ejecutar_analisis(df_nodes, df_elems, df_fix, df_ln, df_le, dim_col, dim_vig):
    ops.wipe()
    ops.model("Basic", "-ndm", 2, "-ndf", 3)
    
    # --- CÁLCULO DE PROPIEDADES DE SECCIÓN (Manual) ---
    # dim_col = [base, altura]
    b_col, h_col = dim_col[0], dim_col[1]
    A1 = b_col * h_col
    I1 = (b_col * h_col**3) / 12
    
    # dim_vig = [base, altura]
    b_vig, h_vig = dim_vig[0], dim_vig[1]
    A2 = b_vig * h_vig
    I2 = (b_vig * h_vig**3) / 12
    
    E = 2.1e7 # Hormigón aprox en Ton/m2
    
    # Geometría
    for r in df_nodes.itertuples(): ops.node(int(r.Node_ID), float(r.X), float(r.Y))
    for r in df_fix.itertuples():   ops.fix(int(r.Node_ID), int(r.Ux), int(r.Uy), int(r.Uz))
    ops.geomTransf("Linear", 1)
    
    for r in df_elems.itertuples():
        sec = int(r.Seccion_ID)
        if sec == 1: A, I = A1, I1 # Columnas
        else:        A, I = A2, I2 # Vigas
        
        # Elemento elástico
        ops.element("elasticBeamColumn", int(r.Element_ID), int(r.i), int(r.j), float(A), float(E), float(I), 1)
        
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    
    # Cargas
    if not df_le.empty:
        for r in df_le.itertuples():
            ops.eleLoad("-ele", int(r.Element_ID), "-type", "-beamUniform", float(r.Wy), 0.0)
            
    if not df_ln.empty:
        for r in df_ln.itertuples():
            ops.load(int(r.Node_ID), float(r.Fx), float(r.Fy), float(r.Mz))
            
    ops.system("BandGeneral")
    ops.numberer("RCM")
    ops.constraints("Plain")
    ops.integrator("LoadControl", 1.0)
    ops.algorithm("Linear")
    ops.analysis("Static")
    return ops.analyze(1)

# --- 4. INTERFAZ GRÁFICA ---

# --- SIDEBAR: DIMENSIONES ---
st.sidebar.header("1. Secciones (Rectangulares)")
c1, c2 = st.sidebar.columns(2)
with c1:
    st.markdown("**Columnas**")
    b_col = st.number_input("Base Col (m)", 0.1, 2.0, 0.30)
    h_col = st.number_input("Altura Col (m)", 0.1, 2.0, 0.30)
with c2:
    st.markdown("**Vigas**")
    b_vig = st.number_input("Base Viga (m)", 0.1, 2.0, 0.25)
    h_vig = st.number_input("Altura Viga (m)", 0.1, 2.0, 0.40)

st.sidebar.divider()

# --- SIDEBAR: GEOMETRÍA ---
st.sidebar.header("2. Geometría Vertical")
n_pisos = st.sidebar.number_input("Número de Pisos", 1, 20, 3)
h_piso  = st.sidebar.number_input("Altura de Entrepiso (m)", 1.0, 10.0, 3.0)

st.sidebar.divider()

# --- SIDEBAR: VANOS Y CARGAS ---
st.sidebar.header("3. Vanos y Cargas")
st.sidebar.caption("Tabla de Vanos (Carga Vertical)")
data_vanos = pd.DataFrame([{"Longitud (m)": 5.0, "Carga Vert. (Ton/m)": -2.0}, {"Longitud (m)": 4.0, "Carga Vert. (Ton/m)": -2.0}])
df_vanos = st.sidebar.data_editor(data_vanos, num_rows="dynamic", hide_index=True, column_config={"Longitud (m)": st.column_config.NumberColumn(format="%.2f m"), "Carga Vert. (Ton/m)": st.column_config.NumberColumn(format="%.2f T/m")})

st.sidebar.caption("Tabla de Sismo (Carga Lateral)")
data_sismo = pd.DataFrame([{"Piso N°": 1, "Fuerza X (Ton)": 10.0}, {"Piso N°": 2, "Fuerza X (Ton)": 15.0}])
df_sismo = st.sidebar.data_editor(data_sismo, num_rows="dynamic", hide_index=True, column_config={"Piso N°": st.column_config.NumberColumn(format="%d", step=1), "Fuerza X (Ton)": st.column_config.NumberColumn(format="%.2f Ton")})

# Generación
df_nodes, df_fix, df_elems, df_ln, df_le = generar_modelo_completo(n_pisos, h_piso, df_vanos, df_sismo)

# --- 5. RESULTADOS ---
st.subheader("Modelo Generado")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Pisos", n_pisos); c2.metric("Vanos", len(df_vanos)); c3.metric("Sección Col", f"{b_col}x{h_col}"); c4.metric("Sección Viga", f"{b_vig}x{h_vig}")

if st.button("🚀 Calcular Pórtico", type="primary"):
    if df_vanos.empty:
        st.error("Agrega al menos un vano.")
    else:
        with st.spinner("Calculando..."):
            # Pasamos las dimensiones a la función de análisis
            ok = ejecutar_analisis(df_nodes, df_elems, df_fix, df_ln, df_le, [b_col, h_col], [b_vig, h_vig])
        
        if ok == 0:
            st.success("Cálculo Exitoso")
            
            # Pestañas
            tab1, tab2, tab3, tab4 = st.tabs(["Estructura", "Axiales (N)", "Cortantes (Vy)", "Momentos (Mz)"])
            
            # --- CÓDIGO DE VISUALIZACIÓN PERSONALIZADO ---
            # Escalas sugeridas (puedes cambiarlas aquí si se ven muy chicas o grandes
            sfacN = 8.e-2   # Escala Axiales
            sfacV = 8.e-2   # Escala Cortantes
            sfacM = 8.e-2   # Escala Momentos

            with tab1:
                st.write("**Estructura y Cargas Aplicadas**")
                plt.close('all')
                # Muestra el modelo + Cargas (plot_load)
                opsv.plot_model()
                opsv.plot_load() 
                st.pyplot(plt.gcf())

            with tab2:
                st.write("**Diagrama Axial (N)**")
                plt.close('all')
                # Usamos sfacN como pediste
                opsv.section_force_diagram_2d('N', sfacN)
                st.pyplot(plt.gcf())

            with tab3:
                st.write("**Diagrama Cortante (Vy)**")
                plt.close('all')
                # Nota: En opsvis standard se usa 'Vy'. Si tienes una versión modificada usa 'T'
                opsv.section_force_diagram_2d('T', sfacV)
                st.pyplot(plt.gcf())

            with tab4:
                st.write("**Diagrama Momento (M)**")
                plt.close('all')
                # Nota: En opsvis standard se usa 'Mz'.
                opsv.section_force_diagram_2d('M', sfacM)
                st.pyplot(plt.gcf())

        else:
            st.error("Error en el análisis.")
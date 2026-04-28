import os
import streamlit

# 1. Find the internal Streamlit folder on the Render server
st_dir = os.path.dirname(streamlit.__file__)
index_path = os.path.join(st_dir, "static", "index.html")

# 2. Read the raw Streamlit source file
with open(index_path, "r", encoding="utf-8") as file:
    html = file.read()

# 3. Replace the default name with CotiListo
html = html.replace("<title>Streamlit</title>", "<title>CotiListo</title>")

# 4. Save the modification
with open(index_path, "w", encoding="utf-8") as file:
    file.write(html)

print("✅ Streamlit source file patched successfully: Name changed to CotiListo!")
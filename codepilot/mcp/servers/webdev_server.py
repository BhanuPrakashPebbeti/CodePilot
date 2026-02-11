"""Web development MCP server for HTML, CSS, JS tooling."""

import logging
import os
import json
from pathlib import Path

from fastmcp import FastMCP

app = FastMCP(name="webdev")


@app.tool()
def create_html_boilerplate(file_path: str, title: str = "My App") -> str:
    """Create HTML5 boilerplate file.
    
    Args:
        file_path: Path where HTML file should be created.
        title: Page title.
    
    Returns:
        Success message.
    """
    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <link rel="stylesheet" href="styles.css">
</head>
<body>
    <div id="root"></div>
    <script src="script.js"></script>
</body>
</html>"""
    
    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(html_content)
        return f"HTML boilerplate created: {file_path}"
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def create_react_component(file_path: str, component_name: str, is_functional: bool = True) -> str:
    """Create React component boilerplate.
    
    Args:
        file_path: Path where component file should be created.
        component_name: Name of the component.
        is_functional: True for functional component, False for class component.
    
    Returns:
        Success message.
    """
    if is_functional:
        content = f"""import React from 'react';

const {component_name} = () => {{
    return (
        <div className="{component_name.lower()}">
            <h1>{component_name}</h1>
        </div>
    );
}};

export default {component_name};
"""
    else:
        content = f"""import React, {{ Component }} from 'react';

class {component_name} extends Component {{
    render() {{
        return (
            <div className="{component_name.lower()}">
                <h1>{component_name}</h1>
            </div>
        );
    }}
}}

export default {component_name};
"""
    
    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"React component created: {file_path}"
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def create_package_json(directory: str, name: str, description: str = "") -> str:
    """Create package.json for Node.js project.
    
    Args:
        directory: Project directory.
        name: Package name.
        description: Package description.
    
    Returns:
        Success message.
    """
    package_data = {
        "name": name,
        "version": "1.0.0",
        "description": description,
        "main": "index.js",
        "scripts": {
            "start": "node index.js",
            "test": "echo \"Error: no test specified\" && exit 1"
        },
        "keywords": [],
        "author": "",
        "license": "ISC",
        "dependencies": {}
    }
    
    try:
        file_path = Path(directory) / "package.json"
        file_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(package_data, f, indent=2)
        
        return f"package.json created: {file_path}"
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def create_vite_config(directory: str, framework: str = "react") -> str:
    """Create Vite configuration file.
    
    Args:
        directory: Project directory.
        framework: Framework (react, vue, svelte, vanilla).
    
    Returns:
        Success message.
    """
    plugin_map = {
        "react": "import react from '@vitejs/plugin-react'",
        "vue": "import vue from '@vitejs/plugin-vue'",
        "svelte": "import { svelte } from '@sveltejs/vite-plugin-svelte'",
        "vanilla": ""
    }
    
    plugin_import = plugin_map.get(framework, plugin_map["react"])
    plugin_use = f"plugins: [{framework}()]," if framework != "vanilla" else "plugins: [],"
    
    content = f"""import {{ defineConfig }} from 'vite'
{plugin_import}

export default defineConfig({{
  {plugin_use}
  server: {{
    port: 3000,
    open: true
  }},
  build: {{
    outDir: 'dist'
  }}
}})
"""
    
    try:
        file_path = Path(directory) / "vite.config.js"
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f"Vite config created: {file_path}"
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def create_css_reset(file_path: str) -> str:
    """Create CSS reset/normalize file.
    
    Args:
        file_path: Path where CSS file should be created.
    
    Returns:
        Success message.
    """
    css_content = """/* CSS Reset */
*, *::before, *::after {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
}

html, body {
    height: 100%;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif;
    line-height: 1.6;
}

img, picture, video, canvas, svg {
    display: block;
    max-width: 100%;
}

input, button, textarea, select {
    font: inherit;
}

p, h1, h2, h3, h4, h5, h6 {
    overflow-wrap: break-word;
}

#root, #__next {
    isolation: isolate;
}
"""
    
    try:
        Path(file_path).parent.mkdir(parents=True, exist_ok=True)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(css_content)
        return f"CSS reset created: {file_path}"
    except Exception as e:
        return f"Error: {e}"


@app.tool()
def create_gitignore(directory: str, template: str = "node") -> str:
    """Create .gitignore file with common patterns.
    
    Args:
        directory: Project directory.
        template: Template type (node, python, java, react).
    
    Returns:
        Success message.
    """
    templates = {
        "node": """node_modules/
dist/
build/
.env
.env.local
*.log
.DS_Store
coverage/
""",
        "python": """__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
ENV/
env/
.env
*.egg-info/
dist/
build/
.pytest_cache/
""",
        "react": """node_modules/
dist/
build/
.env.local
.env.development.local
.env.test.local
.env.production.local
npm-debug.log*
yarn-debug.log*
yarn-error.log*
.DS_Store
coverage/
""",
        "java": """*.class
*.jar
*.war
*.ear
target/
.idea/
*.iml
.classpath
.project
.settings/
"""
    }
    
    content = templates.get(template, templates["node"])
    
    try:
        file_path = Path(directory) / ".gitignore"
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return f".gitignore created: {file_path}"
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    os.environ["FASTMCP_CLI_MODE"] = "production"
    logging.getLogger().setLevel(logging.ERROR)
    
    app.run(
        transport="stdio",
        show_banner=False,
        log_level="error"
    )

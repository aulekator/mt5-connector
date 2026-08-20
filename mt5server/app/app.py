import logging
import os
from flask import Flask
from dotenv import load_dotenv
import MetaTrader5 as mt5
from flasgger import Swagger
from werkzeug.middleware.proxy_fix import ProxyFix
from swagger import swagger_config

# Import routes
from routes.health import health_bp
from routes.account import account_bp
from routes.auth import auth_bp
from routes.mt5 import mt5_bp

load_dotenv()
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.config['PREFERRED_URL_SCHEME'] = 'https'

swagger = Swagger(app, config=swagger_config)

# Register blueprints
app.register_blueprint(health_bp)
app.register_blueprint(account_bp)
app.register_blueprint(auth_bp)
app.register_blueprint(mt5_bp)

app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

if __name__ == '__main__':
    # Bounded wait: on a fresh container the terminal may not be logged in
    # yet; initialize(timeout=0) would block the server forever.
    if not mt5.initialize(timeout=15000):
        logger.error("Failed to initialize MT5.")
    app.run(host='0.0.0.0', port=int(os.environ.get('MT5_API_PORT')))
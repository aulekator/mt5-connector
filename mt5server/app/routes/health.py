from flask import Blueprint, jsonify
import MetaTrader5 as mt5
from flasgger import swag_from

health_bp = Blueprint('health', __name__)

@health_bp.route('/health')
@swag_from({
    'tags': ['Health'],
    'responses': {
        200: {
            'description': 'Health check successful',
            'schema': {
                'type': 'object',
                'properties': {
                    'status': {'type': 'string'},
                    'mt5_connected': {'type': 'boolean'},
                    'mt5_initialized': {'type': 'boolean'}
                }
            }
        }
    }
})
def health_check():
    """
    Health Check Endpoint
    ---
    description: Check the health status of the application and MT5 connection.
    responses:
      200:
        description: Health check successful
    """
    # Bounded wait: report initialization state instead of blocking the
    # request when the terminal has not been logged in yet.
    initialized = mt5.initialize(timeout=5000) if mt5 is not None else False
    return jsonify({
        "status": "healthy",
        "mt5_connected": mt5 is not None,
        "mt5_initialized": initialized
    }), 200
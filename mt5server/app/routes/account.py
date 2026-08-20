from flask import Blueprint, jsonify
import MetaTrader5 as mt5
from flasgger import swag_from
import logging

account_bp = Blueprint('account', __name__)
logger = logging.getLogger(__name__)

@account_bp.route('/account', methods=['GET'])
@swag_from({
    'tags': ['Account'],
    'responses': {
        200: {
            'description': 'Account information retrieved successfully.',
            'schema': {
                'type': 'object',
                'properties': {
                    'login': {'type': 'integer'},
                    'name': {'type': 'string'},
                    'server': {'type': 'string'},
                    'currency': {'type': 'string'},
                    'balance': {'type': 'number'},
                    'equity': {'type': 'number'},
                    'margin': {'type': 'number'},
                    'free_margin': {'type': 'number'},
                    'margin_level': {'type': 'number'},
                    'leverage': {'type': 'integer'},
                    'profit': {'type': 'number'},
                    'margin_free_mode': {'type': 'boolean'},
                    'margin_call_mode': {'type': 'integer'},
                    'trade_allowed': {'type': 'boolean'},
                    'trade_expert': {'type': 'boolean'}
                }
            }
        },
        404: {
            'description': 'Failed to get account information.'
        },
        500: {
            'description': 'Internal server error.'
        }
    }
})
def get_account_info():
    """
    Get Account Information
    ---
    description: Retrieve detailed account information including balance, equity, margin, and trading permissions.
    """
    try:
        account_info = mt5.account_info()
        if account_info is None:
            return jsonify({"error": "Failed to get account information"}), 404
        
        account_dict = account_info._asdict()
        return jsonify(account_dict)
    
    except Exception as e:
        logger.error(f"Error in get_account_info: {str(e)}")
        return jsonify({"error": "Internal server error"}), 500
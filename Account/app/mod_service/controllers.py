# -*- coding: utf-8 -*-

# Import dependencies
import json
import uuid
import logging
import bcrypt  # https://github.com/pyca/bcrypt/, https://pypi.python.org/pypi/bcrypt/2.0.0
#from Crypto.Hash import SHA512
#from Crypto.Random.random import StrongRandom
from random import randint
from time import time

# Import flask dependencies
from flask import Blueprint, render_template, make_response, flash, session
from flask.ext.login import login_user, login_required
from flask_restful import Resource, Api, reqparse

# Import the database object from the main app module
from app import db, api, login_manager, app

# Import services
from app.helpers import get_custom_logger, ApiError, get_utc_time
from app.mod_blackbox.controllers import get_account_public_key, generate_and_sign_jws
from app.mod_database.helpers import get_db_cursor


# create logger with 'spam_application'
from app.mod_database.models import SurrogateId

logger = get_custom_logger('mod_service_controllers')


def sign_slr(account_id=None, slr_payload=None, endpoint="sign_slr(account_id, slr_payload, endpoint)"):
    if account_id is None:
        raise AttributeError("Provide account_id as parameter")
    if slr_payload is None:
        raise AttributeError("Provide slr_payload as parameter")

    logger.info("Signing Service Link Record")

    # Get Account owner's public key
    try:
        account_public_key, account_kid = get_account_public_key(account_id=account_id)
        account_public_key_log_entry = account_public_key
        account_public_key = json.loads(account_public_key)
    except Exception as exp:
        logger.error("Could not get account owner's public key: " + repr(exp))
        raise ApiError(code=500, title="Failed to get account owner's public key", detail=repr(exp), source=endpoint)
    else:
        logger.info("Account owner's public key and kid fetched")
    finally:
        logger.debug("account_public_key: " + account_public_key_log_entry)

    # Fill Account key to cr_keys
    try:
        keys = []
        keys.append(account_public_key)
        slr_payload['cr_keys'] = keys
    except Exception as exp:
        logger.error("Could not fill account owner's public key to cr_keys: " + repr(exp))
        raise ApiError(code=500, title="Failed to fill account owner's public key to cr_keys", detail=repr(exp), source=endpoint)
    else:
        logger.info("Account owner's public key added to cr_keys")

    # Fill timestamp to created in slr
    try:
        timestamp_to_fill = get_utc_time()
    except Exception as exp:
        logger.error("Could not get UTC time: " + repr(exp))
        raise ApiError(code=500, title="Could not get UTC time", detail=repr(exp), source=endpoint)
    else:
        logger.info("timestamp_to_fill: " + timestamp_to_fill)

    timestamp_to_fill = int(time())
    try:
        slr_payload['created'] = timestamp_to_fill
    except Exception as exp:
        logger.error("Could not fill timestamp to created in slr: " + repr(exp))
        raise ApiError(code=500, title="Failed to fill timestamp to created in slr", detail=repr(exp), source=endpoint)
    else:
        logger.info("Timestamp filled to created in slr")

    # Sign slr
    slr_signed = {}
    try:
        slr_signed = generate_and_sign_jws(account_id=account_id, jws_payload=json.dumps(slr_payload))
    except Exception as exp:
        logger.error('Could not create Service Link Record: ' + repr(exp))
        raise ApiError(code=500, title="Failed to create Service Link Record", detail=repr(exp), source=endpoint)
    else:
        logger.info('Service Link Record created and signed')
        return slr_signed
    finally:
        logger.debug("slr_payload: " + json.dumps(slr_payload))
        logger.debug("slr_signed: " + slr_signed)


def sign_ssr(account_id=None, ssr_payload=None, endpoint="sign_ssr(account_id, slr_payload, endpoint)"):
    if account_id is None:
        raise AttributeError("Provide account_id as parameter")
    if ssr_payload is None:
        raise AttributeError("Provide ssr_payload as parameter")

    logger.info("Signing Service Link Status Record")

    # Fill timestamp to created in slr
    try:
        timestamp_to_fill = get_utc_time()
    except Exception as exp:
        logger.error("Could not get UTC time: " + repr(exp))
        raise ApiError(code=500, title="Could not get UTC time", detail=repr(exp), source=endpoint)
    else:
        logger.info("timestamp_to_fill: " + timestamp_to_fill)

    try:
        ssr_payload['iat'] = timestamp_to_fill
    except Exception as exp:
        logger.error("Could not fill timestamp to iat in ssr_payload: " + repr(exp))
        raise ApiError(code=500, title="Failed to fill timestamp to iat in ssr_payload", detail=repr(exp), source=endpoint)
    else:
        logger.info("Timestamp filled to created in ssr_payload")

    # Sign ssr
    ssr_signed = {}
    try:
        ssr_signed = generate_and_sign_jws(account_id=account_id, jws_payload=json.dumps(ssr_payload))
    except Exception as exp:
        logger.error('Could not create Service Link Status Record: ' + repr(exp))
        raise ApiError(code=500, title="Failed to create Service Link Record", detail=repr(exp), source=endpoint)
    else:
        logger.info('Service Link Status Record created and signed')
        return ssr_signed, timestamp_to_fill
    finally:
        logger.debug("ssr_payload: " + json.dumps(ssr_payload))
        logger.debug("ssr_signed: " + ssr_signed)


def store_slr_and_ssr(slr_entry=None, ssr_entry=None, endpoint="sign_ssr(account_id, slr_payload, endpoint)"):
    if slr_entry is None:
        raise AttributeError("Provide slr_entry as parameter")
    if ssr_entry is None:
        raise AttributeError("Provide ssr_entry as parameter")

    # Get DB cursor
    try:
        cursor = get_db_cursor()
    except Exception as exp:
        logger.error('Could not get database cursor: ' + repr(exp))
        raise ApiError(code=500, title="Failed to get database cursor", detail=repr(exp), source=endpoint)

    try:
        cursor = slr_entry.to_db(cursor=cursor)

        # Set linking id for ssr and slr
        slr_id = slr_entry.id
        ssr_entry.service_link_records_id = slr_id

        cursor = ssr_entry.to_db(cursor=cursor)

        data = {'slr_id': slr_id, 'ssr_id': ssr_entry.id}

        db.connection.commit()
    except Exception as exp:
        logger.debug('commit failed: ' + repr(exp))
        db.connection.rollback()
        logger.debug('--> rollback')
        raise ApiError(code=500, title="Failed to store slr and ssr", detail=repr(exp), source=endpoint)
    else:
        logger.debug('Slr and Ssr commited')
        return data
    finally:
        logger.debug("slr_entry: " + slr_entry.log_entry)
        logger.debug("ssr_entry: " + ssr_entry.log_entry)


def get_surrogate_id_by_account_and_service(account_id=None, service_id=None, endpoint="(get_surrogate_id_by_account_and_Service)"):
    if account_id is None:
        raise AttributeError("Provide account_id as parameter")
    if service_id is None:
        raise AttributeError("Provide service_id as parameter")

    # Create Surrogate id object
    try:
        sur_id_obj = SurrogateId(service_id=service_id, account_id=account_id)
    except Exception as exp:
        logger.error('Could not create SurrogateId object: ' + repr(exp))
        raise
    else:
        logger.info("SurrogateId object created")
    finally:
        logger.debug("sur_id_obj: " + sur_id_obj.log_entry)

    # Get DB cursor
    try:
        cursor = get_db_cursor()
    except Exception as exp:
        logger.error('Could not get database cursor: ' + repr(exp))
        raise

    try:
        cursor = sur_id_obj.from_db(cursor=cursor)
    except Exception as exp:
        logger.error('Could not get surrogate id from db: ' + repr(exp))
        raise
    else:
        logger.debug("Got sur_id_obj:" + json.dumps(sur_id_obj.to_dict))
        return sur_id_obj.to_dict
    finally:
        logger.debug("sur_id_obj: " + sur_id_obj.log_entry)



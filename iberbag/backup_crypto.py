"""Versioned streaming AES-256-GCM envelope; no embedded key or plaintext archive kept."""
import hashlib
import json
import os
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.exceptions import InvalidTag

MAGIC=b'IBERBAG-BACKUP-2\0'
ITERATIONS=600_000
CHUNK=1024*1024


def key(password,salt):
    if not isinstance(password,str) or not 16<=len(password)<=1024:
        raise ValueError('Senha de cifra obrigatória: 16–1024 caracteres; guardar separadamente.')
    return hashlib.pbkdf2_hmac('sha256',password.encode(),salt,ITERATIONS,32)


def aad(manifest):
    return json.dumps({k:manifest[k] for k in ('format','created_at','inventory','encryption')},sort_keys=True,separators=(',',':')).encode()


def encrypt(source,target,password,manifest):
    salt=os.urandom(16);nonce=os.urandom(12);header=MAGIC+salt+nonce
    cipher=Cipher(algorithms.AES(key(password,salt)),modes.GCM(nonce)).encryptor()
    cipher.authenticate_additional_data(header+aad(manifest));target.write(header)
    while chunk:=source.read(CHUNK): target.write(cipher.update(chunk))
    target.write(cipher.finalize());target.write(cipher.tag)


def decrypt(source,target,password,manifest):
    header=source.read(len(MAGIC)+28)
    if len(header)!=len(MAGIC)+28 or not header.startswith(MAGIC): raise ValueError('Backup cifrado inválido/alterado.')
    source.seek(0,2);remaining=source.tell()-len(header)-16
    if remaining<0: raise ValueError('Backup cifrado truncado/alterado.')
    source.seek(-16,2);tag=source.read(16);source.seek(len(header))
    cipher=Cipher(algorithms.AES(key(password,header[len(MAGIC):len(MAGIC)+16])),modes.GCM(header[-12:],tag)).decryptor()
    cipher.authenticate_additional_data(header+aad(manifest))
    try:
        while remaining:
            chunk=source.read(min(CHUNK,remaining))
            if not chunk: raise ValueError('Backup cifrado truncado/alterado.')
            remaining-=len(chunk);target.write(cipher.update(chunk))
        target.write(cipher.finalize());target.seek(0)
    except InvalidTag as error: raise ValueError('Backup alterado, senha incorreta ou autenticação inválida.') from error

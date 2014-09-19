""" test doing things with keys/signatures/etc
"""
import pytest

import glob
import os
import warnings

from pgpy import PGPKey
from pgpy import PGPMessage
from pgpy import PGPSignature

from pgpy.errors import PGPError
from pgpy.constants import CompressionAlgorithm
from pgpy.constants import PubKeyAlgorithm
from pgpy.constants import SignatureType
from pgpy.constants import SymmetricKeyAlgorithm
from pgpy.constants import KeyFlags
from pgpy.constants import HashAlgorithm

from pgpy.packet.packets import OnePassSignature
from pgpy.packet.packets import PKESessionKey


def _pgpmessage(f):
    msg = PGPMessage()
    msg.parse(f)
    return msg

def _pgpkey(f):
    key = PGPKey()
    key.parse(f)
    return key

def _pgpsignature(f):
    sig = PGPSignature()
    sig.parse(f)
    return sig


class TestPGPMessage(object):
    params = {
        'comp_alg': [ CompressionAlgorithm.Uncompressed, CompressionAlgorithm.ZIP, CompressionAlgorithm.ZLIB,
                      CompressionAlgorithm.BZ2 ],
        'enc_msg':  [ _pgpmessage(f) for f in glob.glob('tests/testdata/messages/message*.pass*.asc') ],
        'lit':      [ PGPMessage.new('tests/testdata/lit') ],
    }
    def test_new_message(self, comp_alg, write_clean, gpg_import, gpg_print):
        msg = PGPMessage.new('tests/testdata/lit', compression=comp_alg)

        if comp_alg == CompressionAlgorithm.Uncompressed:
            assert msg.type == 'literal'
        else:
            assert msg.type == 'compressed'
        assert msg.message.decode('latin-1') == 'This is stored, literally\!\n\n'

        with write_clean('tests/testdata/cmsg.asc', 'w', str(msg)):
            assert gpg_print('cmsg.asc') == msg.message.decode('latin-1')

    def test_decrypt_passphrase_message(self, enc_msg):
        decmsg = enc_msg.decrypt("QwertyUiop")

        assert isinstance(decmsg, PGPMessage)
        assert decmsg.message == b"This is stored, literally\\!\n\n"

    def test_encrypt_passphrase_message(self, lit, write_clean, gpg_decrypt):
        lit.encrypt("QwertyUiop")

        assert lit.type == 'encrypted'

        # decrypt with PGPy
        decmsg = lit.decrypt("QwertyUiop")
        assert isinstance(decmsg, PGPMessage)
        assert decmsg.type == 'compressed'
        assert decmsg.message == b"This is stored, literally\\!\n\n"

        # decrypt with GPG
        with write_clean('tests/testdata/semsg.asc', 'w', str(lit)):
            assert gpg_decrypt('./semsg.asc', "QwertyUiop") == 'This is stored, literally\!\n\n'


class TestPGPKey(object):
    params = {
        'pub':        [ _pgpkey(f) for f in sorted(glob.glob('tests/testdata/keys/*.pub.asc')) ],
        'sec':        [ _pgpkey(f) for f in sorted(glob.glob('tests/testdata/keys/*.sec.asc')) ],
        'enc':        [ _pgpkey(f) for f in sorted(glob.glob('tests/testdata/keys/*.enc.asc')) ],
        'msg':        [ _pgpmessage(f) for f in sorted(glob.glob('tests/testdata/messages/message*.signed*.asc') +
                                                       glob.glob('tests/testdata/messages/cleartext*.signed*.asc')) ],
        'rsa_encmsg': [ _pgpmessage(f) for f in sorted(glob.glob('tests/testdata/messages/message*.rsa*.asc')) ],
        'sigkey':     [ _pgpkey(f) for f in sorted(glob.glob('tests/testdata/signatures/*.key.asc')) ],
        'sigsig':     [ _pgpsignature(f) for f in sorted(glob.glob('tests/testdata/signatures/*.sig.asc')) ],
        'sigsubj':    sorted(glob.glob('tests/testdata/signatures/*.subj'))
    }
    targettes = [ _pgpkey(f) for f in sorted(glob.glob('tests/testdata/keys/targette*.asc')) ]
    ikeys = [os.path.join(*f.split(os.path.sep)[-2:]) for f in glob.glob('tests/testdata/keys/*.pub.asc')]

    def test_unlock(self, enc, sec):
        assert enc.is_protected
        assert not enc.is_unlocked
        assert not sec.is_protected

        # try to sign without unlocking
        with pytest.raises(PGPError):
            enc.sign('tests/testdata/lit')

        # try to unlock with the wrong password
        enc.unlock('ClearlyTheWrongPassword')

        # unlock with the correct passphrase
        with enc.unlock('QwertyUiop'), warnings.catch_warnings():
            warnings.simplefilter('ignore')
            assert enc.is_unlocked
            # sign lit
            sig = enc.sign('tests/testdata/lit')
            # verify with the unlocked key and its unprotected friend
            assert enc.verify('tests/testdata/lit', sig)
            assert sec.verify('tests/testdata/lit', sig)

    def test_verify_detached(self, sigkey, sigsig, sigsubj):
        assert sigkey.verify(sigsubj, sigsig)

    def test_verify_message(self, msg):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for pub in self.params['pub']:
                assert pub.verify(msg)

    def test_verify_self(self, pub):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            assert pub.verify(pub)

    def test_verify_revochiio(self):
        k = PGPKey()
        k.parse('tests/testdata/revochiio.asc')

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sv = k.verify(k)

        assert len(sv._subjects) == 13
        _svtypes = [ s.signature.type for s in sv._subjects ]
        assert SignatureType.CertRevocation in _svtypes
        assert SignatureType.DirectlyOnKey in _svtypes
        assert SignatureType.KeyRevocation in _svtypes
        assert SignatureType.Positive_Cert in _svtypes
        assert SignatureType.Subkey_Binding in _svtypes
        assert SignatureType.PrimaryKey_Binding in _svtypes
        assert SignatureType.SubkeyRevocation in _svtypes
        assert sv

    def test_verify_wrongkey(self):
        wrongkey = PGPKey()
        wrongkey.parse('tests/testdata/signatures/aptapproval-test.key.asc')

        sig = PGPSignature()
        sig.parse('tests/testdata/signatures/debian-sid.sig.asc')

        with pytest.raises(PGPError):
            wrongkey.verify('tests/testdata/signatures/debian-sid.subj', sig)

    def test_verify_invalid(self, sec):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sig = sec.sign('tests/testdata/lit')
            assert not sec.verify('tests/testdata/lit2', sig)

    def test_sign_detach(self, sec, write_clean, gpg_import, gpg_verify):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sig = sec.sign('tests/testdata/lit')

            # Verify with PGPy
            assert sec.verify('tests/testdata/lit', sig)

        # verify with GPG
        with write_clean('tests/testdata/lit.sig', 'w', str(sig)), \
                gpg_import(*[os.path.join(*f.split(os.path.sep)[-2:]) for f in glob.glob('tests/testdata/keys/*.pub.asc')]):
            assert gpg_verify('./lit', './lit.sig', keyid=sig.signer)

    def test_sign_cleartext(self, write_clean, gpg_import, gpg_verify):
        msg = PGPMessage.new('tests/testdata/lit_de', cleartext=True)

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for sec in self.params['sec']:
                msg.add_signature(sec.sign(msg, inline=True))

            assert len(msg.__sig__) == len(self.params['sec'])

            # verify with PGPy
            for pub in self.params['pub']:
                assert pub.verify(msg)

        # verify with GPG
        with write_clean('tests/testdata/lit_de.asc', 'w', str(msg)), \
                gpg_import(*[os.path.join(*f.split(os.path.sep)[-2:]) for f in glob.glob('tests/testdata/keys/*.pub.asc')]):
            assert gpg_verify('./lit_de.asc')

    def test_sign_message(self, write_clean, gpg_import, gpg_verify):
        msg = PGPMessage.new('tests/testdata/lit')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for sec in self.params['sec']:
                msg.add_signature(sec.sign(msg), onepass=False)

            assert not any(isinstance(pkt, OnePassSignature) for pkt in msg._contents)

            # verify with PGPy
            for pub in self.params['pub']:
                assert pub.verify(msg)

        # verify with GPG
        with write_clean('tests/testdata/lit.asc', 'w', str(msg)), \
                gpg_import(*[os.path.join(*f.split(os.path.sep)[-2:]) for f in glob.glob('tests/testdata/keys/*.pub.asc')]):
            assert gpg_verify('./lit.asc')

    def test_onepass_sign_message(self, write_clean, gpg_import, gpg_verify):
        msg = PGPMessage.new('tests/testdata/lit')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            for sec in self.params['sec']:
                msg.add_signature(sec.sign(msg))

            assert not any(isinstance(pkt, OnePassSignature) for pkt in msg._contents)

            # verify with PGPy
            for pub in self.params['pub']:
                assert pub.verify(msg)

        # verify with GPG
        with write_clean('tests/testdata/lit.asc', 'w', str(msg)), \
                gpg_import(*[os.path.join(*f.split(os.path.sep)[-2:]) for f in glob.glob('tests/testdata/keys/*.pub.asc')]):
            assert gpg_verify('./lit.asc')

    def test_sign_timestamp(self, sec):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            tsig = sec.sign(None, sigtype=SignatureType.Timestamp)
            # verify with PGPy only; GPG does not support timestamp signatures
            assert sec.verify(None, tsig)

    def test_sign_userid(self, sec, pub, write_clean, gpg_import, gpg_check_sigs):
        for tk in self.targettes:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                # sign tk's primary uid generically
                tk.userids[0].add_signature(sec.sign(tk.userids[0]))

                # verify with PGPy
                assert pub.verify(tk.userids[0])

            # verify with GnuPG
            tkfp = '{:s}.asc'.format(tk.fingerprint.shortid)
            ikeys = self.ikeys
            ikeys.append(os.path.join('.', tkfp))
            with write_clean(os.path.join('tests', 'testdata', tkfp), 'w', str(tk)), gpg_import(*ikeys):
                assert gpg_check_sigs(tk.fingerprint.keyid)

    def test_revoke_certification(self, sec, pub, write_clean, gpg_import, gpg_check_sigs):
        for tk in self.targettes:
            # we should have already signed the key in test_sign_userid above
            assert sec.fingerprint.keyid in tk.userids[0].signers

            with warnings.catch_warnings():
                # revoke that certification!
                tk.userids[0].add_signature(sec.sign(tk.userids[0], sigtype=SignatureType.CertRevocation))

                # verify with PGPy
                assert pub.verify(tk.userids[0])

            # verify with GnuPG
            tkfp = '{:s}.asc'.format(tk.fingerprint.shortid)
            ikeys = self.ikeys
            ikeys.append(os.path.join('.', tkfp))
            with write_clean(os.path.join('tests', 'testdata', tkfp), 'w', str(tk)), gpg_import(*ikeys):
                assert gpg_check_sigs(tk.fingerprint.keyid)

    def test_sign_key(self):
        pytest.skip("not implemented yet")

    def test_revoke_key(self):
        pytest.skip("not implemented yet")

    def test_sign_subkey(self):
        pytest.skip("not implemented yet")

    def test_revoke_subkey(self):
        pytest.skip("not implemented yet")

    def test_bind_subkey(self):
        pytest.skip("not implemented yet")

    def test_decrypt_rsa_message(self, rsa_encmsg):
        key = PGPKey()
        key.parse('tests/testdata/keys/rsa.1.sec.asc')

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            decmsg = key.decrypt(rsa_encmsg)

        assert isinstance(decmsg, PGPMessage)
        assert decmsg.message == bytearray(b"This is stored, literally\\!\n\n")

    def test_encrypt_rsa_message(self, write_clean, gpg_import, gpg_decrypt):
        pub = PGPKey()
        pub.parse('tests/testdata/keys/rsa.1.pub.asc')
        sec = PGPKey()
        sec.parse('tests/testdata/keys/rsa.1.sec.asc')
        msg = PGPMessage.new('tests/testdata/lit')

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            encmsg = pub.encrypt(msg)
            assert isinstance(encmsg, PGPMessage)
            assert encmsg.is_encrypted

            # decrypt with PGPy
            decmsg = sec.decrypt(encmsg)
            assert isinstance(decmsg, PGPMessage)
            assert not decmsg.is_encrypted
            assert decmsg.message == bytearray(b'This is stored, literally\!\n\n')

        # decrypt with GPG
        with write_clean('tests/testdata/aemsg.asc', 'w', str(encmsg)), gpg_import('keys/rsa.1.sec.asc'):
            assert gpg_decrypt('./aemsg.asc') == 'This is stored, literally\!\n\n'

    def test_encrypt_rsa_multi(self, write_clean, gpg_import, gpg_decrypt):
        msg = PGPMessage.new('tests/testdata/lit')

        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sk = SymmetricKeyAlgorithm.AES256.gen_key()
            for rkey in [ k for k in self.params['pub'] if k.key_algorithm == PubKeyAlgorithm.RSAEncryptOrSign ]:
                msg = rkey.encrypt(msg, sessionkey=sk)

            assert isinstance(msg, PGPMessage)
            assert msg.is_encrypted

            # decrypt with PGPy
            for rkey in [ k for k in self.params['sec'] if k.key_algorithm == PubKeyAlgorithm.RSAEncryptOrSign ]:
                decmsg = rkey.decrypt(msg)

                assert not decmsg.is_encrypted
                assert decmsg.message == b'This is stored, literally\!\n\n'

        with write_clean('tests/testdata/aemsg.asc', 'w', str(msg)):
            for kp in glob.glob('tests/testdata/keys/rsa*.sec.asc'):
                with gpg_import(os.path.join(*kp.split(os.path.sep)[-2:])):
                    assert gpg_decrypt('./aemsg.asc') == 'This is stored, literally\!\n\n'

    def test_add_uid(self, sec, pub, write_clean, gpg_import):
        sec.add_uid('Seconduser Aidee',
                 comment='Temporary',
                 email="seconduser.aidee@notarealemailaddress.com",
                 usage=[KeyFlags.Authentication],
                 hashprefs=[HashAlgorithm.SHA256, HashAlgorithm.SHA1],
                 cipherprefs=[SymmetricKeyAlgorithm.AES128, SymmetricKeyAlgorithm.CAST5],
                 compprefs=[CompressionAlgorithm.ZIP, CompressionAlgorithm.Uncompressed],
                 primary=False)

        u = next(k for k in sec.userids if k.name == 'Seconduser Aidee')
        # assert not u.primary
        assert u.is_uid
        assert u.name == 'Seconduser Aidee'
        assert u.comment == 'Temporary'
        assert u.email == 'seconduser.aidee@notarealemailaddress.com'
        assert u._signatures[0].type == SignatureType.Positive_Cert
        assert u._signatures[0].hashprefs == [HashAlgorithm.SHA256, HashAlgorithm.SHA1]
        assert u._signatures[0].cipherprefs == [SymmetricKeyAlgorithm.AES128, SymmetricKeyAlgorithm.CAST5]
        assert u._signatures[0].compprefs == [CompressionAlgorithm.ZIP, CompressionAlgorithm.Uncompressed]

        # verify with PGPy
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            assert pub.verify(sec)

        # verify with GPG
        tkfp = '{:s}.asc'.format(sec.fingerprint.shortid)
        with write_clean(os.path.join('tests', 'testdata', tkfp), 'w', str(sec)), \
                gpg_import(os.path.join('.', tkfp)) as kio:
            assert 'invalid self-signature' not in kio

        # remove Seconduser Aidee
        sec.del_uid('Seconduser Aidee')
        assert 'Seconduser Aidee' not in [u.name for u in sec.userids]

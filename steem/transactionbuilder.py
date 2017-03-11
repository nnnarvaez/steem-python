import logging

from steem.wallet import Wallet
from steembase.account import PrivateKey
from steembase.exceptions import (
    InsufficientAuthorityError,
    MissingKeyError,
    InvalidKeyFormat
)
from steembase.operations import Operation
from steembase.transactions import SignedTransaction, fmt_time_from_now, get_block_params

from .account import Account
from .instance import shared_steem_instance

log = logging.getLogger(__name__)


class TransactionBuilder(dict):
    """ This class simplifies the creation of transactions by adding
        operations and signers.
    """

    def __init__(self, tx=None, steem_instance=None, no_broadcast=False, expiration=30):
        self.steem = steem_instance or shared_steem_instance()
        self.no_broadcast = no_broadcast
        self.expiration = expiration
        self.wallet = Wallet(self.steem)

        self.op = []
        self.wifs = []
        if tx and not isinstance(tx, dict):
            raise ValueError("Invalid TransactionBuilder Format")
        super(TransactionBuilder, self).__init__(tx or {})

    def appendOps(self, ops):
        if isinstance(ops, list):
            for op in ops:
                self.op.append(op)
        else:
            self.op.append(ops)
        self.constructTx()

    def appendSigner(self, account, permission):
        account = Account(account, steem_instance=self.steem)
        if permission == "active":
            wif = self.wallet.getActiveKeyForAccount(account["name"])
        elif permission == "posting":
            wif = self.wallet.getPostingKeyForAccount(account["name"])
        elif permission == "owner":
            wif = self.wallet.getOwnerKeyForAccount(account["name"])
        else:
            raise ValueError("Invalid permission")
        self.wifs.append(wif)

    def appendWif(self, wif):
        if wif:
            try:
                PrivateKey(wif)
                self.wifs.append(wif)
            except:
                raise InvalidKeyFormat

    def constructTx(self):
        if isinstance(self.op, list):
            ops = [Operation(o) for o in self.op]
        else:
            ops = [Operation(self.op)]
        expiration = fmt_time_from_now(self.expiration)
        ref_block_num, ref_block_prefix = get_block_params(self.steem)
        tx = SignedTransaction(
            ref_block_num=ref_block_num,
            ref_block_prefix=ref_block_prefix,
            expiration=expiration,
            operations=ops
        )
        super(TransactionBuilder, self).__init__(tx.json())

    def sign(self):
        """ Sign a provided transaction witht he provided key(s)

            :param dict tx: The transaction to be signed and returned
            :param string wifs: One or many wif keys to use for signing
                a transaction. If not present, the keys will be loaded
                from the wallet as defined in "missing_signatures" key
                of the transactions.
        """
        try:
            signedtx = SignedTransaction(**self.json())
        except:
            raise ValueError("Invalid TransactionBuilder Format")

        if not any(self.wifs):
            raise MissingKeyError

        signedtx.sign(self.wifs, chain=self.steem.chain_params)
        self["signatures"].extend(signedtx.json().get("signatures"))

    def broadcast(self):
        """ Broadcast a transaction to the Steem network

            :param tx tx: Signed transaction to broadcast
        """
        if self.no_broadcast:
            log.warning("Not broadcasting anything!")
            return self

        try:
            if not self.steem.verify_authority(self.json()):
                raise InsufficientAuthorityError
        except Exception as e:
            raise e

        try:
            self.steem.broadcast_transaction(self.json())
        except Exception as e:
            raise e

        return self

    def addSigningInformation(self, account, permission):
        """ This is a private method that adds side information to a
            unsigned/partial transaction in order to simplify later
            signing (e.g. for multisig or coldstorage)
        """
        accountObj = Account(account, steem_instance=self.steem)
        authority = accountObj[permission]
        # We add a required_authorities to be able to identify
        # how to sign later. This is an array, because we
        # may later want to allow multiple operations per tx
        self.update({"required_authorities": {
            account: authority
        }})
        for account_auth in authority["account_auths"]:
            account_auth_account = Account(account_auth[0], steem_instance=self.steem)
            self["required_authorities"].update({
                account_auth[0]: account_auth_account.get(permission)
            })

        # Try to resolve required signatures for offline signing
        self["missing_signatures"] = [
            x[0] for x in authority["key_auths"]
            ]
        # Add one recursion of keys from account_auths:
        for account_auth in authority["account_auths"]:
            account_auth_account = Account(account_auth[0], steem_instance=self.steem)
            self["missing_signatures"].extend(
                [x[0] for x in account_auth_account[permission]["key_auths"]]
            )
        self["blockchain"] = self.steem.chain_params

    def json(self):
        return dict(self)

    def appendMissingSignatures(self, wifs):
        missing_signatures = self.get("missing_signatures", [])
        for pub in missing_signatures:
            wif = self.wallet.getPrivateKeyForPublicKey(pub)
            if wif:
                self.appendWif(wif)
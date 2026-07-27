import logging
from dataclasses import dataclass
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from config import AppConfig

COGNITO_PROVIDER_PREFIX = "cognito-idp.{region}.amazonaws.com/{user_pool_id}"
logger = logging.getLogger(__name__)
_BOTO_CONFIG = Config(connect_timeout=10, read_timeout=15, retries={"max_attempts": 2})

class AuthError(Exception):
    """Raised for any failure during sign-in or credential exchange."""

class MfaRequired(AuthError):
    """Raised when the sign-in step requires a SOFTWARE_TOKEN_MFA response."""
    def __init__(self, session: str, username: str):
        super().__init__("SOFTWARE_TOKEN_MFA challenge required")
        self.session = session
        self.username = username

@dataclass(frozen=True)
class AssumedCredentials:
    access_key_id: str
    secret_access_key: str
    session_token: str

    def as_boto_kwargs(self) -> dict:
        return {
            "aws_access_key_id": self.access_key_id,
            "aws_secret_access_key": self.secret_access_key,
            "aws_session_token": self.session_token,
        }

def initiate_login(config: AppConfig, username: str, password: str) -> str:
    client = boto3.client("cognito-idp", region_name=config.region, config=_BOTO_CONFIG)
    try:
        response = client.initiate_auth(
            ClientId=config.app_client_id,
            AuthFlow="USER_PASSWORD_AUTH",
            AuthParameters={"USERNAME": username, "PASSWORD": password},
        )
    except ClientError as exc:
        raise AuthError(_friendly_message(exc)) from exc

    challenge_name = response.get("ChallengeName")
    if challenge_name == "SOFTWARE_TOKEN_MFA":
        raise MfaRequired(session=response["Session"], username=username)
    if challenge_name:
        raise AuthError(f"Unsupported auth challenge: {challenge_name}")

    result = response.get("AuthenticationResult")
    if not result:
        raise AuthError("Sign-in did not return an authentication result.")
    return result["IdToken"]

def respond_to_mfa_challenge(config: AppConfig, username: str, session: str, totp_code: str) -> str:
    client = boto3.client("cognito-idp", region_name=config.region, config=_BOTO_CONFIG)
    try:
        response = client.respond_to_auth_challenge(
            ClientId=config.app_client_id,
            ChallengeName="SOFTWARE_TOKEN_MFA",
            Session=session,
            ChallengeResponses={
                "USERNAME": username,
                "SOFTWARE_TOKEN_MFA_CODE": totp_code,
            },
        )
    except ClientError as exc:
        raise AuthError(_friendly_message(exc)) from exc

    result = response.get("AuthenticationResult")
    if not result:
        raise AuthError("MFA challenge did not return an authentication result.")
    return result["IdToken"]

def get_identity_pool_credentials(config: AppConfig, id_token: str) -> AssumedCredentials:
    identity_client = boto3.client("cognito-identity", region_name=config.region, config=_BOTO_CONFIG)
    provider_name = COGNITO_PROVIDER_PREFIX.format(region=config.region, user_pool_id=config.user_pool_id)
    logins = {provider_name: id_token}

    try:
        identity_response = identity_client.get_id(IdentityPoolId=config.identity_pool_id, Logins=logins)
        identity_id = identity_response["IdentityId"]

        creds_response = identity_client.get_credentials_for_identity(IdentityId=identity_id, Logins=logins)
    except ClientError as exc:
        raise AuthError(_friendly_message(exc)) from exc

    creds = creds_response["Credentials"]
    return AssumedCredentials(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretKey"],
        session_token=creds["SessionToken"],
    )

def assume_cross_account_role(config: AppConfig, identity_pool_credentials: AssumedCredentials, session_name: str = "s3-reader-app") -> AssumedCredentials:
    sts_client = boto3.client(
        "sts",
        region_name=config.region,
        config=_BOTO_CONFIG,
        **identity_pool_credentials.as_boto_kwargs(),
    )
    try:
        response = sts_client.assume_role(
            RoleArn=config.cross_account_role_arn,
            RoleSessionName=session_name,
        )
    except ClientError as exc:
        raise AuthError(_friendly_message(exc)) from exc

    creds = response["Credentials"]
    return AssumedCredentials(
        access_key_id=creds["AccessKeyId"],
        secret_access_key=creds["SecretAccessKey"],
        session_token=creds["SessionToken"],
    )

def sign_in_and_get_bucket_credentials(config: AppConfig, id_token: str) -> AssumedCredentials:
    identity_pool_credentials = get_identity_pool_credentials(config, id_token)
    return assume_cross_account_role(config, identity_pool_credentials)

def _friendly_message(exc: ClientError) -> str:
    error = exc.response.get("Error", {})
    code = error.get("Code", "UnknownError")
    message = error.get("Message", str(exc))
    return f"{code}: {message}"
import pytest, jwt, time, os
from fastapi_jwt2 import AuthJWT
from fastapi_jwt2.exceptions import AuthJWTException
from fastapi import FastAPI, Depends, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from pydantic_settings import BaseSettings


@pytest.fixture(scope="function")
def client():
    app = FastAPI()

    @app.exception_handler(AuthJWTException)
    def authjwt_exception_handler(request: Request, exc: AuthJWTException):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})

    @app.get("/protected")
    def protected(Authorize: AuthJWT = Depends()):
        Authorize.jwt_required()
        return {"hello": "world"}

    @app.get("/raw_token")
    def raw_token(Authorize: AuthJWT = Depends()):
        Authorize.jwt_required()
        return Authorize.get_raw_jwt()

    @app.get("/get_subject")
    def get_subject(Authorize: AuthJWT = Depends()):
        Authorize.jwt_required()
        return Authorize.get_jwt_subject()

    @app.get("/refresh_token")
    def get_refresh_token(Authorize: AuthJWT = Depends()):
        Authorize.jwt_refresh_token_required()
        return Authorize.get_jwt_subject()

    client = TestClient(app)
    return client


@pytest.fixture(scope="function")
def default_access_token():
    return {
        "jti": "123",
        "sub": "test",
        "type": "access",
        "fresh": True,
    }


@pytest.fixture()
def test_settings() -> None:
    class TestSettings(BaseSettings):
        AUTHJWT_SECRET_KEY: str = "secret-key"
        AUTHJWT_ACCESS_TOKEN_EXPIRES: int = 1
        AUTHJWT_REFRESH_TOKEN_EXPIRES: int = 1
        AUTHJWT_DECODE_LEEWAY: int = 2

    @AuthJWT.load_config
    def load():
        return TestSettings()


@pytest.fixture(scope="function")
def encoded_token(default_access_token):
    return jwt.encode(default_access_token, "secret-key", algorithm="HS256")


def test_verified_token(client, encoded_token, Authorize):
    class SettingsOne(BaseSettings):
        AUTHJWT_SECRET_KEY: str = "secret-key"
        AUTHJWT_ACCESS_TOKEN_EXPIRES: int = 2

    @AuthJWT.load_config
    def get_settings_one():
        return SettingsOne()

    # DecodeError
    response = client.get("/protected", headers={"Authorization": "Bearer test"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Not enough segments"}
    # InvalidSignatureError
    token = jwt.encode({"some": "payload"}, "secret", algorithm="HS256")
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Signature verification failed"}
    # ExpiredSignatureError
    token = Authorize.create_access_token(subject="test")
    time.sleep(3)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    assert response.json() == {"detail": "Signature has expired"}
    # InvalidAlgorithmError
    token = jwt.encode({"some": "payload"}, "secret", algorithm="HS384")
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": "The specified alg value is not allowed"}

    class SettingsTwo(BaseSettings):
        AUTHJWT_SECRET_KEY: str = "secret-key"
        AUTHJWT_ACCESS_TOKEN_EXPIRES: int = 1
        AUTHJWT_REFRESH_TOKEN_EXPIRES: int = 1
        AUTHJWT_DECODE_LEEWAY: int = 2

    @AuthJWT.load_config
    def get_settings_two():
        return SettingsTwo()

    access_token = Authorize.create_access_token(subject="test")
    refresh_token = Authorize.create_refresh_token(subject="test")
    pair_token = Authorize.create_pair_token(subject="test")
    pair_access_token = pair_token["access_token"]
    pair_refresh_token = pair_token["refresh_token"]
    time.sleep(2)
    # JWT payload is now expired
    # But with some leeway, it will still validate
    # Access
    response = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}
    # Refresh
    response = client.get("/refresh_token", headers={"Authorization": f"Bearer {refresh_token}"})
    assert response.status_code == 200
    assert response.json() == "test"
    # Pair
    response = client.get("/protected", headers={"Authorization": f"Bearer {pair_access_token}"})
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}
    response = client.get("/refresh_token", headers={"Authorization": f"Bearer {pair_refresh_token}"})
    assert response.status_code == 200
    assert response.json() == "test"

    # Valid Token
    response = client.get("/protected", headers={"Authorization": f"Bearer {encoded_token}"})
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}


def test_get_raw_token(client, default_access_token, encoded_token, test_settings):
    response = client.get("/raw_token", headers={"Authorization": f"Bearer {encoded_token}"})
    assert response.status_code == 200
    assert response.json() == default_access_token


def test_get_raw_jwt(default_access_token, encoded_token, Authorize, test_settings):
    assert Authorize.get_raw_jwt(encoded_token) == default_access_token


def test_get_jwt_jti(client, default_access_token, encoded_token, Authorize, test_settings):
    assert Authorize.get_jti(encoded_token=encoded_token) == default_access_token["jti"]


def test_get_jwt_subject(client, default_access_token, encoded_token, test_settings):
    response = client.get("/get_subject", headers={"Authorization": f"Bearer {encoded_token}"})
    assert response.status_code == 200
    assert response.json() == default_access_token["sub"]


def test_invalid_jwt_issuer(client, Authorize, test_settings):
    # No issuer claim expected or provided - OK
    token = Authorize.create_access_token(subject="test")
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}

    AuthJWT._decode_issuer = "urn:foo"

    # Issuer claim expected and not provided - Not OK
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": 'Token is missing the "iss" claim'}

    AuthJWT._decode_issuer = "urn:foo"
    AuthJWT._encode_issuer = "urn:bar"

    # Issuer claim still expected and wrong one provided - not OK
    token = Authorize.create_access_token(subject="test")
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid issuer"}

    AuthJWT._decode_issuer = None
    AuthJWT._encode_issuer = None


@pytest.mark.parametrize("token_aud", ["foo", ["bar"], ["foo", "bar", "baz"]])
def test_valid_aud(client, Authorize, token_aud, test_settings):
    AuthJWT._decode_audience = ["foo", "bar"]

    access_token = Authorize.create_access_token(subject=1, audience=token_aud)
    response = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}

    refresh_token = Authorize.create_refresh_token(subject=1, audience=token_aud)
    response = client.get("/refresh_token", headers={"Authorization": f"Bearer {refresh_token}"})
    assert response.status_code == 200
    assert response.json() == 1

    if token_aud == ["foo", "bar", "baz"]:
        AuthJWT._decode_audience = None


@pytest.mark.parametrize("token_aud", ["bar", ["bar"], ["bar", "baz"]])
def test_invalid_aud_and_missing_aud(client, Authorize, token_aud, test_settings):
    AuthJWT._decode_audience = "foo"

    access_token = Authorize.create_access_token(subject=1, audience=token_aud)
    response = client.get("/protected", headers={"Authorization": f"Bearer {access_token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": "Audience doesn't match"}

    refresh_token = Authorize.create_refresh_token(subject=1)
    response = client.get("/refresh_token", headers={"Authorization": f"Bearer {refresh_token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": 'Token is missing the "aud" claim'}

    if token_aud == ["bar", "baz"]:
        AuthJWT._decode_audience = None


def test_invalid_decode_algorithms(client, Authorize, test_settings):
    class SettingsAlgorithms(BaseSettings):
        authjwt_secret_key: str = "secret"
        authjwt_decode_algorithms: list = ["HS384", "RS256"]

    @AuthJWT.load_config
    def get_settings_algorithms():
        return SettingsAlgorithms()

    token = Authorize.create_access_token(subject=1)
    response = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": "The specified alg value is not allowed"}

    AuthJWT._decode_algorithms = None


def test_valid_asymmetric_algorithms(client, Authorize, test_settings):
    hs256_token = Authorize.create_access_token(subject=1)

    DIR = os.path.abspath(os.path.dirname(__file__))
    private_txt = os.path.join(DIR, "private_key.txt")
    public_txt = os.path.join(DIR, "public_key.txt")

    with open(private_txt) as f:
        PRIVATE_KEY = f.read().strip()

    with open(public_txt) as f:
        PUBLIC_KEY = f.read().strip()

    class SettingsAsymmetric(BaseSettings):
        authjwt_algorithm: str = "RS256"
        authjwt_secret_key: str = "secret"
        authjwt_private_key: str = PRIVATE_KEY
        authjwt_public_key: str = PUBLIC_KEY

    @AuthJWT.load_config
    def get_settings_asymmetric():
        return SettingsAsymmetric()

    rs256_token = Authorize.create_access_token(subject=1)

    response = client.get("/protected", headers={"Authorization": f"Bearer {hs256_token}"})
    assert response.status_code == 422
    assert response.json() == {"detail": "The specified alg value is not allowed"}

    response = client.get("/protected", headers={"Authorization": f"Bearer {rs256_token}"})
    assert response.status_code == 200
    assert response.json() == {"hello": "world"}


def test_invalid_asymmetric_algorithms(client, Authorize, test_settings):
    class SettingsAsymmetricOne(BaseSettings):
        authjwt_algorithm: str = "RS256"

    @AuthJWT.load_config
    def get_settings_asymmetric_one():
        return SettingsAsymmetricOne()

    with pytest.raises(RuntimeError, match=r"authjwt_private_key"):
        Authorize.create_access_token(subject=1)

    DIR = os.path.abspath(os.path.dirname(__file__))
    private_txt = os.path.join(DIR, "private_key.txt")

    with open(private_txt) as f:
        PRIVATE_KEY = f.read().strip()

    class SettingsAsymmetricTwo(BaseSettings):
        authjwt_algorithm: str = "RS256"
        authjwt_private_key: str = PRIVATE_KEY

    @AuthJWT.load_config
    def get_settings_asymmetric_two():
        return SettingsAsymmetricTwo()

    token = Authorize.create_access_token(subject=1)
    with pytest.raises(RuntimeError, match=r"authjwt_public_key"):
        client.get("/protected", headers={"Authorization": f"Bearer {token}"})

    AuthJWT._private_key = None
    AuthJWT._public_key = None
    AuthJWT._algorithm = "HS256"
    AuthJWT._secret_key = "secret"

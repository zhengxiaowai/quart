import pytest

from quart import abort, jsonify, Quart, request, Response, ResponseReturnValue


@pytest.fixture
def app() -> Quart:
    app = Quart(__name__)

    @app.route('/')
    def index() -> ResponseReturnValue:
        return 'index'

    @app.route('/json/', methods=['POST'])
    async def json() -> ResponseReturnValue:
        data = await request.get_json()
        return jsonify(data['value'])

    @app.route('/error/')
    async def error() -> ResponseReturnValue:
        abort(409)
        return 'OK'

    @app.errorhandler(409)
    async def handler(_: Exception) -> ResponseReturnValue:
        return 'Something Unique', 409

    return app


@pytest.mark.asyncio
async def test_index(app: Quart) -> None:
    test_client = app.test_client()
    response = await test_client.get('/')
    assert response.status_code == 200
    assert b'index' in (await response.get_data())


@pytest.mark.asyncio
async def test_json(app: Quart) -> None:
    test_client = app.test_client()
    response = await test_client.post('/json/', json={'value': 'json'})
    assert response.status_code == 200
    assert b'json' in (await response.get_data())


@pytest.mark.asyncio
async def test_error(app: Quart) -> None:
    test_client = app.test_client()
    response = await test_client.get('/error/')
    assert response.status_code == 409
    assert b'Something Unique' in (await response.get_data())


@pytest.mark.asyncio
async def test_make_response_str(app: Quart) -> None:
    response = await app.make_response('Result')
    assert response.status_code == 200
    assert (await response.get_data()) == b'Result'

    response = await app.make_response(('Result', {'name': 'value'}))
    assert response.status_code == 200
    assert (await response.get_data()) == b'Result'
    assert response.headers['name'] == 'value'

    response = await app.make_response(('Result', 404, {'name': 'value'}))
    assert response.status_code == 404
    assert (await response.get_data()) == b'Result'
    assert response.headers['name'] == 'value'


@pytest.mark.asyncio
async def test_make_response_response(app: Quart) -> None:
    response = await app.make_response(Response('Result'))
    assert response.status_code == 200
    assert (await response.get_data()) == b'Result'

    response = await app.make_response((Response('Result'), {'name': 'value'}))
    assert response.status_code == 200
    assert (await response.get_data()) == b'Result'
    assert response.headers['name'] == 'value'

    response = await app.make_response((Response('Result'), 404, {'name': 'value'}))
    assert response.status_code == 404
    assert (await response.get_data()) == b'Result'
    assert response.headers['name'] == 'value'

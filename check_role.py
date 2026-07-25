from app import app
from model import User

with app.app_context():
    for u in User.query.all():
        print(f"User: {u.username}, Role: {u.role}, RoleType: {type(u.role)}, RoleValue: {getattr(u.role, 'value', u.role)}")

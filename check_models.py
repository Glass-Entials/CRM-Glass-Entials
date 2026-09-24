with open('model.py', 'r', encoding='utf-8') as f:
    content = f.read()
print('SaaSSubscription class:', 'class SaaSSubscription(db.Model)' in content)
print('SaaSTransaction class:', 'class SaaSTransaction(db.Model)' in content)
print('SaaSWebhookEvent class:', 'class SaaSWebhookEvent(db.Model)' in content)

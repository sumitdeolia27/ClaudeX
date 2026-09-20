---
id: 10
slug: security
title: Security
profile: deep
depends_on: 4, 5, 6
---

## Goal
Cover authentication, authorization, data protection, and OWASP Top 10.

## Checklist
- HTTPS
- Secure authentication
- Password hashing with bcrypt / Argon2
- OAuth / Firebase / auth provider if required
- MFA optionally
- Role-based authorization
- JWT / session security
- Secure cookies
- CSRF protection
- XSS prevention
- SQL / NoSQL injection prevention
- Input validation
- Input sanitization
- Rate limiting
- Brute-force protection
- CAPTCHA if required
- File upload validation
- File-size limits
- Allowed file types
- Virus scanning where required
- API key protection
- Secrets in .env
- Never commit secrets to Git
- CORS configuration
- Encryption at rest and in transit
- Sensitive-data masking
- Password reset security
- Email verification
- Account lockout
- Audit logs
- Dependency vulnerability scanning
- Security headers
- OWASP Top 10 checks
- Backup and recovery
- Privacy / data deletion controls

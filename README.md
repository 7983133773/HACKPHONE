# HACKPHONE — Flask + Firebase

Dark cyber-themed account, membership, credits, redeem and manually reviewed UPI request portal.

## Important safety and deployment notes
- Screen sharing is **consent-based** and uses the browser's native screen-share picker. This project does not provide hidden access, remote touch control, password/session theft, or background monitoring.
- Screen sharing only works while the participant actively shares and can stop it at any time.
- Payment screenshots are not proof of payment. Users send screenshots to `@ayushcontact_bot` and include the order ID. Owner must independently verify the UPI transaction before approving. The bot must be configured to receive and forward messages; this website only opens the bot.
- The project is a starter implementation. Test with dummy accounts and tiny/no-value test plans before production.
- Because payment collection involves money, have a parent/guardian oversee any real transactions and comply with applicable rules.
- Never commit service-account keys, SMTP credentials, or `.env` to GitHub.
- Do not put a real owner password in source code. Create the owner account in Firebase Authentication and set `users/{uid}/is_owner = true` manually in Realtime Database.

## Firebase setup
1. Create a Firebase project.
2. Enable **Authentication → Email/Password**.
3. Create a Realtime Database.
4. Create a Storage bucket.
5. In Project settings → General, copy the Web API key.
6. In Project settings → Service accounts, generate a private key. Keep it secret.
7. Add the service account JSON as Render env var `FIREBASE_SERVICE_ACCOUNT_JSON` (single-line JSON), and set:
   - `FIREBASE_DATABASE_URL`
   - `FIREBASE_WEB_API_KEY`
8. Set database rules to deny direct client access. The server uses Firebase Admin SDK.

## Create owner
1. In Firebase Authentication, manually create the owner account using an email you control.
2. Copy the new user's UID.
3. In Realtime Database, create:
   `users/OWNER_UID`
   with:
   ```json
   {
     "email": "owner-email@example.com",
     "username": "ayush_hackz",
     "credits": 0,
     "plan": "VIP",
     "plan_expires": null,
     "is_owner": true
   }
   ```
4. Set `OWNER_EMAIL` in Render to the same email.
5. Do not reuse a password disclosed in a chat. Use a new strong password and store it securely.

## Gmail password reset / verification email
The app generates Firebase verification/reset links. To send them, configure SMTP env vars in Render. For Gmail, use an App Password with 2-Step Verification; never use your normal Gmail password. Firebase Authentication email templates should also be configured. If SMTP is absent, reset/verification email delivery will not work.

## Render deployment
1. Upload this project to a private GitHub repository.
2. In Render, choose **New → Web Service** and connect the repository.
3. Render detects `render.yaml`; otherwise use:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn --worker-class gthread --threads 100 --timeout 120 app:app`
4. Add all environment variables from `.env.example` in Render. Replace every placeholder.
5. Deploy. Your address will be `https://YOUR-SERVICE-NAME.onrender.com`.
6. Test register, login, reset email, redeem, plan creation, payment request, and owner review with test data.

## UPI
Set `UPI_ID` to the UPI ID you are authorized to use. The app creates a UPI intent QR. The app does not verify bank transactions automatically; the owner must check the actual transaction in their UPI app/bank account before approval.

## Features
- Register/login with Firebase Authentication
- Email verification/reset-link generation (SMTP required for delivery)
- User dashboard, credits and transaction history
- Owner-only user/credit/plan/redeem/payment dashboard
- Bulk credit adjustment
- `AYUSH-` redeem codes with plan, duration, credits and claim limits
- Owner-defined Normal/VIP plans
- UPI QR and manual payment review through Telegram bot `@ayushcontact_bot` (user must include the order ID)
- Owner approve/reject payment requests
- Consent-based WebRTC screen sharing room with explicit browser screen-share prompt

## Known limitations to address before production
- Configure HTTPS (Render provides it) and a strong FLASK_SECRET_KEY.
- Add rate limiting, CSRF protection, audit logging and robust email delivery.
- Review Firebase Storage CORS and bucket IAM.
- Redeem claim transaction should be moved into a single atomic transaction for high-concurrency deployments.
- The minimal WebRTC relay is a starter; production deployments should add TURN infrastructure and stronger session authorization.
- The browser does not allow silent screen capture or generic remote phone control.

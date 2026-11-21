# Split Slip for Vercel

A three-page hostel expense app. This edition serves HTML, CSS and JavaScript from public/ and handles account and ledger requests with one Python Vercel Function at /api/slip. PostgreSQL holds user records, salted password hashes, expiring sessions, groups, comments, receipt files and confirmed settlements.

## Deploy from Windows using a browser

1. Extract the ZIP. The GitHub repository root should contain api/, public/, requirements.txt and vercel.json side by side. Upload the extracted files and folders, then commit. Uploading the ZIP itself will not create the app structure.
2. In Vercel, add a new Project and import that GitHub repository. Choose **Other** if asked for a framework. The root directory is the folder containing vercel.json. Leave the build command empty. The configuration sets public as the output directory. Click Deploy.
3. Connect a Neon PostgreSQL database to that Vercel project through Storage / Marketplace, enabling the Production environment. In Project Settings → Environment Variables, check for DATABASE_URL or POSTGRES_URL. If neither was added, create DATABASE_URL and paste the pooled connection string from the Neon **Connect** dialog, including its TLS settings. Keep this value in Vercel's settings.
4. In Deployments, redeploy the latest production deployment. Open the production URL and choose Get started. The first API request creates the ss_ tables in your database. Use a database role that can create tables in its default schema. No migration command is needed for a fresh install.
5. Sign up and add a small food expense. Log out and sign in again. The expense should still be present. Preview deployments need their own configured database URL if you want to test them. A separate database branch for previews keeps test data separate.

## Try two accounts

- The first account starts with an empty Hostel Buddies group. Add a roommate's name and phone number. Select Invite code next to their name and confirm. Copy the code displayed at the top and share it privately.
- In a second browser profile, sign up with the invited number. Choose Join group and enter the code. Codes expire after 24 hours and can be used once. The app does not send SMS or verify phone ownership.
- Either member can add an expense. Select its payer and the people sharing it. The app splits the total in whole paise, including any rounding remainder. Refresh on the other browser to see the change.
- Bills are mandatory for Electricity and E-commerce, recommended above ₹500 otherwise. JPEG, PNG and PDF files up to 1 MB are downloaded on demand by group members.
- Expand an expense to see its shares and add a comment under your account name. Edit the recipient's UPI ID to enable the payment link. Either party can manually confirm a completed payment. A UPI app opening does not mark the expense paid.
- The exact settlement calculator supports groups up to 12 people. This small-group edition limits each group's ledger text to 850 KB, excluding receipts.

## Preserve your homepage design

The package includes a compact standalone homepage. To use your original homepage, replace public/index.html and copy its style.css, script.js and images into public/ using the same relative paths. Point your Log in link to ./login.htm and your Get Started buttons to ./login.htm#signup. Keep the supplied public/login.htm, account.js and dashboard.htm so the forms use the hosted API.

This edition starts fresh. Accounts and expenses from the earlier local server or browser storage are not automatically imported. The static dashboard shell is public; every request for group data or receipts requires an active session and membership.

## Optional local run

Install Python 3.12, open this folder in VS Code and run **py local.py** in Terminal. Open http://127.0.0.1:8000. Without DATABASE_URL or POSTGRES_URL the local runner uses local.sqlite3 for classroom testing. Vercel never uses this fallback. Do not upload the local database.

To run the included integration check locally: **py tests/smoke.py**. It uses a temporary database and does not use the production URL. It checks the API over HTTP with independent cookie jars, including rejected credentials, duplicate registration, invite restrictions, required bills, permissions, comments, stale writes and duplicate settlements.

## If something goes wrong

- Homepage appears but the human check cannot load: check the database URL for the deployed environment and redeploy. The app shows a setup error when the connection string is missing.
- 404 on /api/slip: check that api/slip.py and vercel.json belong to the same project root, with public/ beside api/.
- “Group changed”: close the dialog, click Refresh and submit your change again. This protects another person's changes from being overwritten.
- A new deployment signs everyone out unexpectedly: check that it still points at the same database. Sessions normally last eight hours.
- Use Vercel's Functions logs for runtime failures. Do not paste database credentials into a screenshot, public repository, or chat.

Official references: https://vercel.com/docs/functions/runtimes/python · https://vercel.com/docs/postgres · https://vercel.com/docs/environment-variables

PRALAY GROCERY STORE — CONNECTED LOCAL DEMO
=========================================
এই সংস্করণে customer storefront ও admin panel একই SQLite database ব্যবহার করে।

যা আছে:
- Product add/edit/delete, price, stock, category, description
- Product image upload (PNG/JPG/JPEG/WEBP/GIF, max 5 MB)
- Admin password change (hash করে database-এ রাখা হয়)
- Coupon code + percentage discount
- Delivery fee এবং store name settings
- Customer cart, checkout form, Cash on Delivery order
- Admin panel-এ order দেখা ও status update

যা এখনো LIVE নয়:
- Online payment gateway চালু করা হয়নি। Razorpay/অন্য gateway-এর account, KYC, API keys,
  server-side payment verification এবং HTTPS লাগবে। শুধু “Online payment” নির্বাচন করলে
  checkout ইচ্ছাকৃতভাবে বন্ধ হবে; কোনো টাকা নেওয়া হবে না।
- Live public hosting, domain, automated delivery integration, SMS/WhatsApp notifications নেই।
- এটি local prototype; production launch-এর আগে security review, CSRF protection, rate limiting,
  HTTPS, backups, privacy/terms/refund policy এবং order/payment tests দরকার।

Windows-এ চালানো:
1) Python 3.11 বা নতুন version ইনস্টল করো (python.org)।
2) ZIP extract করো।
3) extracted folder-এর ভিতরে address bar-এ cmd লিখে Enter চাপো।
4) চালাও:
   py -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt
   py app.py
5) Browser-এ খোলো: http://127.0.0.1:5000
6) Admin: http://127.0.0.1:5000/admin
   প্রথম login:
   username: admin
   temporary password: ChangeMe123!
   লগইন করেই Admin dashboard থেকে password বদলাবে।

গুরুত্বপূর্ণ:
- পুরনো demo password বা অন্য কোনো real account password ব্যবহার কোরো না।
- app.py-তে debug=False আছে। Local demo-কে সরাসরি public internet-এ expose কোরো না।
- store.db ফাইলে product/order/customer তথ্য থাকবে; ফাইলটি ব্যক্তিগত রাখো।
- Admin login-এর পর image upload করতে পারবে; ছবি server-এর static/uploads-এ জমা হবে।
- Coupon admin panel-এ তৈরি করে customer page-এ ব্যবহার করা যাবে।

# AWS Lambda Deployment

This guide explains how to deploy the API handlers on AWS Lambda behind an API Gateway.

## 1. Package the code

```bash
pip install -r requirements.txt -t lambda_package
cp -r aws_lambda_api src lambda_package/
cd lambda_package && zip -r ../lambda-api.zip .
```

Upload `lambda-api.zip` when creating each Lambda function.

## 2. Configure Lambda functions

Create two functions using the Python 3.11 runtime:

- **Tasks API** with handler `handler.handler`
- **AI Chat API** with handler `ai_handler.handler`

In each function, add the environment variables used by the application such as the Firebase and OpenAI credentials.

## 3. Set up API Gateway

Create an HTTP API in API Gateway and attach the Lambda functions as integrations.

Routes:

- `ANY /tasks` -> Tasks API
- `ANY /tasks/{id}` -> Tasks API
- `POST /chat` -> AI Chat API

Enable CORS if the frontend will call these endpoints from a browser.

## 4. Deploy

Deploy the API Gateway stage and note the invoke URL. Requests to `/tasks` and `/chat` will now be processed by the Lambda functions.


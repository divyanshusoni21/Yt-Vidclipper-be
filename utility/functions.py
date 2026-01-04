from threading import Thread
from email_func.email import Email

import csv
import tempfile
import os

from django.conf import settings
# import boto3
from rest_framework import exceptions
from rest_framework.views import  exception_handler
from rest_framework.exceptions import ValidationError
from django.contrib.auth.models import User
from django.contrib.auth.tokens import PasswordResetTokenGenerator
from django.utils.http import urlsafe_base64_encode
from django.utils.encoding import  smart_bytes
from django.utils.encoding import smart_str
from django.utils.http import urlsafe_base64_decode
from .variables import frontendDomain
from datetime import datetime
import secrets
import string

def sendMail(body:dict,email:str,subject:str,csvFilePath:list[str] = None ,fileAttachUrl:dict = None,ccMail:list=[],attachFile=None,fileAttachUrls:list = None):
    # Convert single fileAttachUrl to list format for backward compatibility
    if fileAttachUrl is not None and fileAttachUrls is None:
        fileAttachUrls = [fileAttachUrl]
    
    data = {'email_body': body, 
            'to_email': email,
            'email_subject': subject,
            "csv_files_paths":csvFilePath,
            "cc_mail": ccMail,
            "attach_file":attachFile,
            "file_attach_urls":fileAttachUrls
            }
    t1 = Thread(target=Email.send_email,args=(data,))
    t1.start()

def runSerializer(serializerClass,data,obj = None,request = None) -> tuple :
    ''' creates or updates model object with serializer class , returns object and data as tuple'''
    if obj :
        serializer = serializerClass(obj , data=data , partial = True,context={'request':request})
    else :
        serializer = serializerClass(data=data,context={'request':request})
    
    serializer.is_valid(raise_exception=True)
    obj = serializer.save()
    return (obj,serializer)


def create_temporary_csv_file(columnHeaders:list[str], data:list[list[str]], fileName = "file"):
    
    # tempfile.NamedTemporaryFile is used to create a temporary file that is automatically deleted when it's closed. 
    # we can read from and write to this temporary file just like any other file.
  
    tempFile = tempfile.NamedTemporaryFile(delete=False, mode='w', newline='',encoding='utf-8-sig',prefix=fileName,suffix=".csv")

    # creating a temprory csv file
    with tempFile as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(columnHeaders) # Write column headers
        for record in data:
            writer.writerow(record)
        
    return tempFile

def delete_temporary_file(tempFile):
    tempFile.close()
    os.remove(tempFile.name)

# def generate_s3_presigned_object_url(s3ObjectKey,s3ObjectExpirationTimeInSeconds,download=False):
#     s3BucketName = settings.AWS_STORAGE_BUCKET_NAME

#     s3Client = boto3.client('s3',aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
#                     aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
#                     region_name=settings.AWS_S3_REGION_NAME)

#     # Generate the pre-signed URL
   
#     params = {'Bucket': s3BucketName, 'Key': s3ObjectKey}
#     if download :
#         params['ResponseContentDisposition']= 'attachment'
    
#     presignedUrl = s3Client.generate_presigned_url(
#         'get_object',
#         Params=params,
#         ExpiresIn=s3ObjectExpirationTimeInSeconds,  # Expiration time in seconds 
        
#     )
#     return presignedUrl


def format_validation_errors(exception: Exception, context: dict) :
    if not type(exception) == ValidationError :
        exception = str(exception)
        return exception

    # Call REST framework's default exception handler first,
    # to get the standard error response.
    response = exception_handler(exception, context)


    # Only alter the response when it's a validation error
    if not isinstance(exception, exceptions.ValidationError):
        return response

    # It's a validation error, there should be a Serializer
    view = context.get("view", None)
    serializer = view.get_serializer_class()()
   
    errors_list = []
    print(response.data)
    for key, details in response.data.items() :

        if key in serializer.fields:
            label = serializer.fields[key].label
            help_text = serializer.fields[key].help_text

            for message in details:
                errors_list.append("{}: {}".format(label, message))

        elif key == "non_field_errors":
            for message in details:
                errors_list.append(message)

        else:
            for message in details:
                errors_list.append("{}: {}".format(key, message))
    return errors_list

def get_user_from_uidb(uidb):
    # Decode the token to get user ID
    try:
        
        smartId = smart_str(urlsafe_base64_decode(uidb))
        user = User.objects.get(pk=smartId)
    except (ValueError, User.DoesNotExist, TypeError, OverflowError):
        user = None
    
    return user 

def generate_verification_link(user,type="forget-password"):
    uidb64 = urlsafe_base64_encode(smart_bytes(user.id))
    token = PasswordResetTokenGenerator().make_token(user)
    uidb64 = uidb64 + '.' + token

    if type == "register":
        path = '/register'
    else :
        path = '/forget-password'
        
    domain = frontendDomain

    customUrl = domain + path +"?uidb="+str(uidb64)
    
    return customUrl

def check_token_valid_or_not(token):
    ''' check recieved uidb token is valid and return user else return False'''
    
    # seperate the uidb and jwt token
    uidb64,token = token.split(".")
    # get user from uidb
    user = get_user_from_uidb(uidb64)
    
    if user is not None and PasswordResetTokenGenerator().check_token(user,token) :
        # invalidate the token by updating last login field
        user.last_login = datetime.now() 
        user.save(update_fields=["last_login"])
        return user
    
    return False

def create_unique_id(modelClass,lookUpField, uniqueIdIdentifier,length=6):
    
    ''' get a model class and lookup field and create a slug for it's new object'''

    uniqueId = uniqueIdIdentifier +'-'
    
    while True :
        characters = string.ascii_letters + string.digits
        uniqueId += ''.join(secrets.choice(characters) for _ in range(length))
        # creating a dynamic arguments
        kwargs = {f'{lookUpField}':uniqueId}
        
        if not modelClass.objects.filter(**kwargs):
            break
    return uniqueId

def time_to_seconds(time_str: str) -> int:
    """Converts a time string of format MM:SS or HH:MM:SS to seconds."""
    parts = list(map(int, time_str.split(':')))
    if len(parts) == 2: # MM:SS
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3: # HH:MM:SS
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError("Invalid time format. Use MM:SS or HH:MM:SS.")
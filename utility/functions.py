from threading import Thread
from email_func.brevo_email import Email

from rest_framework import exceptions
from rest_framework.views import  exception_handler
from rest_framework.exceptions import ValidationError


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


def time_to_seconds(time_str: str) -> int:
    """Converts a time string of format MM:SS or HH:MM:SS to seconds."""
    parts = list(map(int, time_str.split(':')))
    if len(parts) == 2: # MM:SS
        return parts[0] * 60 + parts[1]
    elif len(parts) == 3: # HH:MM:SS
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    raise ValueError("Invalid time format. Use MM:SS or HH:MM:SS.")
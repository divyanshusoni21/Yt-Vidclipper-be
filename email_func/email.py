from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string
from django.utils.html import strip_tags
from utility.variables import projectName,adminEmail,frontendDomain,projectLogo
from yt_helper.settings import logger
import traceback
from datetime import datetime
import requests


def attach_file_from_url(url:str,fileType:str,fileName:str,email:EmailMultiAlternatives):
    response = requests.get(url)

    if response.status_code == 200:
    
    # Attach the file to the email
        content = response.content
        if fileType == 'pdf' :
            email.attach(fileName, content, "application/pdf")
        elif fileType == 'csv' :
            email.attach(fileName,content,'text/csv')
        elif fileType == 'mp4' or fileType == 'video':
            email.attach(fileName, content, "video/mp4")
            
    return email

class Email:
    @staticmethod
    def send_email(data):
        try :
      
            currentYear = datetime.now().today().year

            try :
                # getting the html template. given in type keyword
                emailTemplate = data["email_body"]["type"] + ".html"

                # add these variables to pass in html template
                data["email_body"]["projectName"] = projectName
                data["email_body"]["currentYear"] = currentYear
                data["email_body"]["adminEmail"] = adminEmail
                data["email_body"]["frontendDomain"] = frontendDomain
                data["email_body"]["projectLogo"] = projectLogo
   
                # get full html content with the variable passed
                html_content = render_to_string(emailTemplate, data["email_body"])
                text_content = strip_tags(html_content)
            except :
                logger.warning(traceback.format_exc())
                emailBody = str(data["email_body"])
                text_content = emailBody
                html_content = emailBody

            email = EmailMultiAlternatives(data["email_subject"], text_content, to=[data["to_email"]],cc = data["cc_mail"])

            email.attach_alternative(html_content, "text/html")

            # attach csv files if there is any
            csvFilesPaths = data["csv_files_paths"] # a list of files paths that can be attached with email
            if csvFilesPaths is not None :
                for csvFilePath in csvFilesPaths :
                    email.attach_file(csvFilePath, 'text/csv')  # Specify the content type if needed
            
            # if we have files with urls then attach all of them with email
            fileUrls = data.get("file_attach_urls") # fileUrls = [{"url":"the url","file_type":"mp4","file_name":"name"}, ...]
            if fileUrls is not None and isinstance(fileUrls, list):
                for fileUrlItem in fileUrls:
                    url = fileUrlItem["url"]
                    fileType = fileUrlItem["file_type"]
                    fileName = fileUrlItem["file_name"]
                    email = attach_file_from_url(url,fileType,fileName,email)
            
            # attach file like pdf,csv or any other
            attachFile = data["attach_file"] # attachFile = {"file_name":"name","file_content":fileContent,"file_content_type":fileContentType}
            if attachFile is not None :
                email.attach(attachFile["file_name"], attachFile["file_content"], attachFile["file_content_type"])
            
            email.send()
           
                
            logger.info(f'email send to {data["to_email"]} , subject :{data["email_subject"]}')
              
        except Exception as e :
            
            logger.warning(traceback.format_exc())
            pass
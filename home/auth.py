from .models import User
from utility.functions import runSerializer
import traceback
from yt_helper.settings import logger
from rest_framework import viewsets, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from rest_framework.decorators import action
from django.db import transaction
import traceback
from .serializers import UserSerializer
from utility.functions import format_validation_errors,generate_verification_link,check_token_valid_or_not,sendMail

# Create your views here.


class AuthViewSet(viewsets.ModelViewSet):
    permission_classes = [AllowAny]
    serializer_class = UserSerializer

    def get_user_data(self,user:User):
        return  {
            "refresh": str(user.tokens()["refresh"]),
            "access": str(user.tokens()["access"]),
            "email": user.email,
            "username":user.username,
        }


    @action(detail=False, methods=['post'])
    def login(self, request):
        """User login endpoint"""
        try :
            email = request.data["email"]
            password = request.data["password"]

            user = User.objects.filter(email__iexact = email).first()
            
            if user is None :
                raise Exception("please check credentails again !")
            
            if not user.check_password(password):
                raise Exception("please check credentails again !")
            
            if not user.is_active :
                raise Exception(f'User is inactive')
            
            if not user.is_verified :
                raise Exception(f'Account is not verfied')

            userData = self.get_user_data(user)

            return Response(userData,status=status.HTTP_200_OK)

        except Exception as e :
            e = format_validation_errors(e,self.get_exception_handler_context())
            logger.warning(traceback.format_exc())
            return Response({"error":e},status = status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def forget_password(self, request):
        """
        Send forget password email with a reset link to the specified email if user is active.
        """
        try:
            email = request.GET.get("email")
            if not email:
                raise Exception("Email is required!")

            user = User.objects.filter(email__iexact=email, is_active=True).first()

            if not user:
                raise Exception(f'No user found with this email!')

            # Send email with verification link
            verificationUrl = generate_verification_link(user)
            email_body = {
                "username": user.username,
                'link': verificationUrl,
                'type': 'forget_password'
            }
            sendMail(email_body, email, subject='Reset Password')

            return Response({"success": "An email has been sent with reset password link."}, status=status.HTTP_200_OK)

        except Exception as e:
            e = format_validation_errors(e, self.get_exception_handler_context())
            logger.warning(traceback.format_exc())
            return Response({"error": e}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def register(self, request):
        """
        Register a new user with username, email, and optional phone number.
        Creates user without password and sends set password link via email.
        Optionally accepts a referral code.
        """
        try:
            username = request.data.get("username")
            email = request.data.get("email")
            phone = request.data.get("phone")
            password = request.data.get("password")
            
            
            # Validate required fields
            if not email:
                raise Exception("Email is required!")
            if not username:
                username = email.split("@")[0]
            
            # Check if user already exists
            if User.objects.filter(email__iexact=email).exists():
                raise Exception("A user with this email already exists!")

            
            # Create user without password
            user_data = {
                "username": username,
                "email": email,
                "is_active": True,
                "is_verified": False,
                "password":password
            }
            
            if phone:
                user_data["phone"] = phone
            
            # Use runSerializer to create user
            user, serializer = runSerializer(UserSerializer, user_data)
            
            # Generate verification link for setting password
            verificationUrl = generate_verification_link(user,path="/set-password")
            email_body = {
                "username": user.username,
                'link': verificationUrl,
                'type': 'set_password'
            }
            
            sendMail(email_body, email, subject='Set Your Password')
            
            response_data = {
                "success": "User registered successfully. A set password link has been sent to your email.",
                "user_id": user.id,
                "email": user.email,
                "username": user.username
            }
            
            return Response(response_data, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            e = format_validation_errors(e, self.get_exception_handler_context())
            logger.warning(traceback.format_exc())
            transaction.set_rollback(True)
            return Response({"error": e}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def resetpassword(self, request):
        """
        Reset password for a user with a valid reset code.
        Can be used for both password reset and initial password setting.
        """
        try:
            code = request.data.get("code")
            password = request.data.get("password")

            if not code or not password:
                raise Exception("Both code and password are required.")

            user = check_token_valid_or_not(code)

            if not user:
                raise Exception(f'Invalid or expired code')

            # Set password and mark user as verified if not already verified
            user.set_password(password)
            
            
            user.is_verified = True

            user.save()

            # Get user data with tokens
            userData = self.get_user_data(user)

            return Response({
                "success": "Password set successfully. Your account is now verified.",
                **userData
            }, status=status.HTTP_200_OK)

        except Exception as e:
            e = format_validation_errors(e, self.get_exception_handler_context())
            logger.warning(traceback.format_exc())
            transaction.set_rollback(True)  # Manually trigger a rollback so db will return to its previous state
            return Response({"error": e}, status=status.HTTP_400_BAD_REQUEST)

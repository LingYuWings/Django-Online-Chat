from django.shortcuts import render

# Create your views here.
from django.core.mail import send_mail
from django.contrib.auth.models import User
from django.shortcuts import render, redirect
from .models import UserProfile, FriendRequest, ChatRoom, Message
from django.contrib.auth.models import User
from .models import UserProfile
import random
import string

def generate_verification_code():
    return ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(6))

def register(request):
    if request.method == 'POST':
        # process the registration form
        username = request.POST['username']
        email = request.POST['email']
        password = request.POST['password']
        user = User.objects.create_user(username=username, email=email, password=password)
        user.is_active = False
        user.save()

        # create a profile with a verification code
        code = generate_verification_code()
        UserProfile.objects.create(user=user, verification_code=code)

        # send email
        send_mail('Verify your account', f'Your verification code is: {code}', 'from@example.com', [email])

        # redirect to a page where user can enter the verification code
        return redirect('verify_account')

    return render(request, 'register.html')

def verify_account(request):
    if request.method == 'POST':
        code = request.POST['code']
        try:
            profile = UserProfile.objects.get(verification_code=code)
            user = profile.user
            user.is_active = True
            user.save()
            profile.verified = True
            profile.save()
            # log the user in and redirect to home page or dashboard
        except UserProfile.DoesNotExist:
            # handle invalid code
            pass

    return render(request, 'verify_account.html')

def send_friend_request(request, user_id):
    if request.method == 'POST':
        from_user = request.user
        to_user = User.objects.get(id=user_id)
        FriendRequest.objects.create(from_user=from_user, to_user=to_user)
        return redirect('friend_requests')

def accept_friend_request(request, request_id):
    friend_request = FriendRequest.objects.get(id=request_id)
    if request.method == 'POST' and friend_request.to_user == request.user:
        from_user = friend_request.from_user
        to_user = friend_request.to_user
        from_user.userprofile.friends.add(to_user)
        to_user.userprofile.friends.add(from_user)
        friend_request.delete()
        return redirect('friends_list')

def create_chat_room(request):
    if request.method == 'POST':
        selected_users = request.POST.getlist('selected_users')
        chat_room = ChatRoom.objects.create()
        for user_id in selected_users:
            user = User.objects.get(id=user_id)
            chat_room.members.add(user)
        chat_room.save()
        return redirect('chat_room', room_id=chat_room.id)

    users = User.objects.exclude(id=request.user.id)
    return render(request, 'create_chat_room.html', {'users': users})

def chat_room(request, room_id):
    room = ChatRoom.objects.get(id=room_id)
    if request.method == 'POST':
        message = request.POST['message']
        Message.objects.create(room=room, sender=request.user, content=message)

    messages = Message.objects.filter(room=room)
    return render(request, 'chat_room.html', {'room': room, 'messages': messages})

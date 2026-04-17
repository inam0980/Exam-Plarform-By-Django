from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.urls import reverse
from django.utils import timezone
from django.conf import settings as django_settings
import json
import os
import subprocess
import tempfile
import shutil

from .models import UserRole, Exam, StudentExam, Question, Choice, Answer, ProctorSnapshot, PasswordResetOTP, CodingTestCase
import base64
import random
from django.core.mail import send_mail
from django.contrib import messages


# ==================== Local C Compiler ====================
# status_id codes (same as Judge0 for frontend compatibility):
#   3 = Accepted  |  5 = TLE  |  6 = Compile Error  |  11 = Runtime Error

def _execute_c_code(code, stdin):
    """
    Compile and run C code using the local gcc/clang compiler.
    Returns (stdout, stderr, compile_output, status_id, status_desc).
    All file I/O happens in a unique temp directory that is always cleaned up.
    """
    compiler   = getattr(django_settings, 'LOCAL_C_COMPILER',   '/usr/bin/gcc')
    time_limit = getattr(django_settings, 'LOCAL_C_TIME_LIMIT', 5)

    work_dir = tempfile.mkdtemp(prefix='exam_c_')
    src_path = os.path.join(work_dir, 'solution.c')
    bin_path = os.path.join(work_dir, 'solution')

    try:
        with open(src_path, 'w') as f:
            f.write(code)

        # ── Compile ──────────────────────────────────────────────────────────
        try:
            compile_proc = subprocess.run(
                [compiler, src_path, '-o', bin_path, '-lm'],
                capture_output=True, text=True, timeout=15, cwd=work_dir,
            )
        except FileNotFoundError:
            return None, None, f'Compiler not found: {compiler}\nInstall via: brew install gcc', 0, 'Compiler Not Found'
        except subprocess.TimeoutExpired:
            return '', '', 'Compilation timed out.', 6, 'Compilation Error'

        if compile_proc.returncode != 0:
            return '', '', compile_proc.stderr or compile_proc.stdout, 6, 'Compilation Error'

        # ── Run ──────────────────────────────────────────────────────────────
        try:
            run_proc = subprocess.run(
                [bin_path],
                input=stdin or '',
                capture_output=True, text=True,
                timeout=time_limit, cwd=work_dir,
            )
        except subprocess.TimeoutExpired:
            return '', '', '', 5, 'Time Limit Exceeded'

        if run_proc.returncode != 0:
            return run_proc.stdout, run_proc.stderr, '', 11, 'Runtime Error'

        return run_proc.stdout, run_proc.stderr, '', 3, 'Accepted'

    except Exception as exc:
        return None, None, str(exc), 0, 'Error'
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _grade_coding_answer(question, code):
    """
    Run code against all test cases linked to the question.
    Returns (marks_obtained, graded:bool).
    """
    if not code or not code.strip():
        return 0, True

    test_cases = list(question.test_cases.all())
    if not test_cases:
        return None, False

    total = 0
    for tc in test_cases:
        stdout, _stderr, compile_out, status_id, _ = _execute_c_code(code, tc.stdin)
        if stdout is None:
            return None, False  # compiler unavailable — leave for manual review
        if status_id == 3 and stdout.strip() == tc.expected_output.strip():
            total += tc.marks

    return total, True


@require_POST
@login_required(login_url='login')
def run_code(request):
    """Compile & execute student C code locally, return stdout/stderr/status."""
    try:
        data  = json.loads(request.body)
        code  = data.get('code', '').strip()
        stdin = data.get('stdin', '')

        if not code:
            return JsonResponse({'error': 'No code provided'}, status=400)

        stdout, stderr, compile_out, status_id, status_desc = _execute_c_code(code, stdin)

        if stdout is None:
            return JsonResponse({'error': compile_out or 'Compiler service unavailable.'}, status=503)

        return JsonResponse({
            'stdout':         stdout or '',
            'stderr':         stderr or '',
            'compile_output': compile_out or '',
            'status':         status_desc,
            'status_id':      status_id,
        })
    except Exception as exc:
        return JsonResponse({'error': str(exc)}, status=500)


# ==================== Authentication Views ====================

def home(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'auth/home.html')


def signup(request):
    if request.method == 'POST':
        first_name = request.POST.get('first_name', '').strip()
        last_name = request.POST.get('last_name', '').strip()
        reg_number = request.POST.get('reg_number', '').strip()
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')
        role = request.POST.get('role')
        course = request.POST.get('course', '').strip()
        section = request.POST.get('section', '').strip()

        def form_error(msg):
            return render(request, 'auth/signup.html', {
                'error': msg,
                'first_name': first_name,
                'last_name': last_name,
                'reg_number': reg_number,
                'email': email,
                'course': course,
                'section': section,
            })

        # Only admin can create teacher accounts
        if role == 'teacher':
            return form_error('Teacher accounts can only be created by an administrator.')

        if not first_name or not last_name:
            return form_error('First name and last name are required.')

        if not reg_number:
            return form_error('Registration number is required.')

        if not course:
            return form_error('Please select your course.')

        if not section:
            return form_error('Please enter your section.')

        if password != confirm_password:
            return form_error('Passwords do not match.')

        # Use registration number as the username (unique)
        if User.objects.filter(username=reg_number).exists():
            return form_error('An account with this registration number already exists.')

        user = User.objects.create_user(
            username=reg_number,
            email=email,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )
        UserRole.objects.create(user=user, role=role, course=course, section=section)
        login(request, user)
        return redirect('dashboard')

    return render(request, 'auth/signup.html')


def forgot_password(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip()
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return render(request, 'auth/forgot_password.html', {
                'error': 'No account found with this email address.'
            })

        otp = f"{random.randint(100000, 999999)}"
        PasswordResetOTP.objects.create(user=user, otp=otp)

        send_mail(
            subject='Password Reset OTP - Exam Portal',
            message=(
                f'Hello {user.get_full_name() or user.username},\n\n'
                f'Your OTP for password reset is:\n\n    {otp}\n\n'
                f'This OTP is valid for 10 minutes. Do not share it with anyone.\n\n'
                f'If you did not request a password reset, ignore this email.'
            ),
            from_email=None,  # uses DEFAULT_FROM_EMAIL from settings
            recipient_list=[email],
            fail_silently=False,
        )

        request.session['reset_user_id'] = user.id
        return redirect('verify_otp')

    return render(request, 'auth/forgot_password.html')


def verify_otp(request):
    user_id = request.session.get('reset_user_id')
    if not user_id:
        return redirect('forgot_password')

    if request.method == 'POST':
        otp_entered = request.POST.get('otp', '').strip()
        try:
            user = User.objects.get(id=user_id)
            otp_obj = PasswordResetOTP.objects.filter(
                user=user, otp=otp_entered, is_used=False
            ).latest('created_at')

            if otp_obj.is_expired():
                return render(request, 'auth/verify_otp.html', {
                    'error': 'OTP has expired. Please request a new one.',
                    'show_resend': True,
                })

            otp_obj.is_used = True
            otp_obj.save()
            request.session['otp_verified'] = True
            return redirect('reset_password')

        except (User.DoesNotExist, PasswordResetOTP.DoesNotExist):
            return render(request, 'auth/verify_otp.html', {
                'error': 'Invalid OTP. Please try again.',
            })

    return render(request, 'auth/verify_otp.html')


def reset_password(request):
    user_id = request.session.get('reset_user_id')
    otp_verified = request.session.get('otp_verified')

    if not user_id or not otp_verified:
        return redirect('forgot_password')

    if request.method == 'POST':
        password = request.POST.get('password', '')
        confirm = request.POST.get('confirm_password', '')

        if len(password) < 6:
            return render(request, 'auth/reset_password.html', {
                'error': 'Password must be at least 6 characters.'
            })

        if password != confirm:
            return render(request, 'auth/reset_password.html', {
                'error': 'Passwords do not match.'
            })

        user = User.objects.get(id=user_id)
        user.set_password(password)
        user.save()

        # Clear reset session keys
        request.session.pop('reset_user_id', None)
        request.session.pop('otp_verified', None)

        messages.success(request, 'Password reset successfully. Please login with your new password.')
        return redirect('login')

    return render(request, 'auth/reset_password.html')


def login_view(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')
        selected_role = request.POST.get('role')
        user = authenticate(request, username=username, password=password)
        if user is not None:
            try:
                user_role = UserRole.objects.get(user=user)
                if user_role.role != selected_role:
                    role_label = 'Student' if selected_role == 'student' else 'Teacher'
                    return render(request, 'auth/login.html', {
                        'error': f'This account is not registered as a {role_label}.',
                        'selected_role': selected_role,
                    })
            except UserRole.DoesNotExist:
                return render(request, 'auth/login.html', {
                    'error': 'Account role not configured. Contact admin.',
                    'selected_role': selected_role,
                })
            login(request, user)
            return redirect('dashboard')
        else:
            return render(request, 'auth/login.html', {
                'error': 'Invalid username or password',
                'selected_role': selected_role,
            })

    selected_role = request.GET.get('role', 'student')
    return render(request, 'auth/login.html', {'selected_role': selected_role})


@require_POST
def logout_view(request):
    logout(request)
    return redirect('home')


# ==================== Dashboard ====================

@login_required(login_url='login')
def dashboard(request):
    try:
        user_role = UserRole.objects.get(user=request.user)
        if user_role.role == 'teacher':
            return redirect('teacher_dashboard')
        else:
            return redirect('student_dashboard')
    except UserRole.DoesNotExist:
        return render(request, 'error.html', {'error': 'User role not defined'})


# ==================== Teacher Views ====================

@login_required(login_url='login')
def teacher_dashboard(request):
    try:
        user_role = UserRole.objects.get(user=request.user)
        if user_role.role != 'teacher':
            return redirect('student_dashboard')
    except UserRole.DoesNotExist:
        return redirect('home')

    exams = Exam.objects.filter(teacher=request.user)
    return render(request, 'teacher/dashboard.html', {'exams': exams})


@login_required(login_url='login')
def create_exam(request):
    try:
        user_role = UserRole.objects.get(user=request.user)
        if user_role.role != 'teacher':
            return redirect('student_dashboard')
    except UserRole.DoesNotExist:
        return redirect('home')

    if request.method == 'POST':
        title = request.POST.get('title')
        description = request.POST.get('description')
        duration_minutes = int(request.POST.get('duration_minutes'))
        from datetime import datetime as dt
        start_time = timezone.make_aware(dt.fromisoformat(request.POST.get('start_time')))
        end_time = timezone.make_aware(dt.fromisoformat(request.POST.get('end_time')))
        total_marks = int(request.POST.get('total_marks', 100))
        passing_marks = int(request.POST.get('passing_marks', 40))

        exam = Exam.objects.create(
            teacher=request.user,
            title=title,
            description=description,
            duration_minutes=duration_minutes,
            start_time=start_time,
            end_time=end_time,
            total_marks=total_marks,
            passing_marks=passing_marks
        )
        return redirect('add_questions', exam_id=exam.id)

    return render(request, 'teacher/create_exam.html')


@login_required(login_url='login')
def edit_exam(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, teacher=request.user)

    if request.method == 'POST':
        from datetime import datetime as dt
        exam.title = request.POST.get('title', exam.title)
        exam.description = request.POST.get('description', exam.description)
        exam.duration_minutes = int(request.POST.get('duration_minutes', exam.duration_minutes))
        exam.total_marks = int(request.POST.get('total_marks', exam.total_marks))
        exam.passing_marks = int(request.POST.get('passing_marks', exam.passing_marks))
        exam.start_time = timezone.make_aware(dt.fromisoformat(request.POST.get('start_time')))
        exam.end_time = timezone.make_aware(dt.fromisoformat(request.POST.get('end_time')))
        exam.is_published = 'is_published' in request.POST
        exam.save()
        return redirect('teacher_dashboard')

    # Convert stored UTC times to local for pre-filling datetime-local inputs
    start_local = timezone.localtime(exam.start_time)
    end_local = timezone.localtime(exam.end_time)
    return render(request, 'teacher/edit_exam.html', {
        'exam': exam,
        'start_time_str': start_local.strftime('%Y-%m-%dT%H:%M'),
        'end_time_str': end_local.strftime('%Y-%m-%dT%H:%M'),
    })


@login_required(login_url='login')
def add_questions(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, teacher=request.user)

    if request.method == 'POST':
        # Each question is identified by a sequential index prefix q0_, q1_, ...
        indices = sorted({
            key.split('_')[0][1:]
            for key in request.POST
            if key.startswith('q') and '_' in key and key.split('_')[0][1:].isdigit()
        }, key=int)

        for i in indices:
            q_text = request.POST.get(f'q{i}_text', '').strip()
            if not q_text:
                continue
            q_type  = request.POST.get(f'q{i}_type', 'mcq')
            q_marks = int(request.POST.get(f'q{i}_marks', 1) or 1)
            order   = exam.questions.count() + 1

            question = Question.objects.create(
                exam=exam,
                question_text=q_text,
                question_type=q_type,
                marks=q_marks,
                order=order,
            )

            if q_type == 'mcq':
                choice_texts   = request.POST.getlist(f'q{i}_choice_text')
                correct_values = set(request.POST.getlist(f'q{i}_correct'))
                for idx, ct in enumerate(choice_texts):
                    if ct.strip():
                        Choice.objects.create(
                            question=question,
                            choice_text=ct.strip(),
                            is_correct=(str(idx) in correct_values),
                            order=idx + 1,
                        )
            elif q_type == 'true_false':
                tf_correct = request.POST.get(f'q{i}_tf_correct', 'True')
                for idx, label in enumerate(['True', 'False']):
                    Choice.objects.create(
                        question=question,
                        choice_text=label,
                        is_correct=(label == tf_correct),
                        order=idx + 1,
                    )

            elif q_type == 'coding':
                # Use unique per-TC field names (q{i}_tcinput_{tcIdx}, etc.)
                # to avoid positional bugs when test case rows are deleted in the browser.
                prefix_out = f'q{i}_tcoutput_'
                tc_indices = sorted({
                    int(k[len(prefix_out):])
                    for k in request.POST
                    if k.startswith(prefix_out) and k[len(prefix_out):].isdigit()
                })
                total_tc_marks = 0
                for tc_idx in tc_indices:
                    expected = request.POST.get(f'q{i}_tcoutput_{tc_idx}', '').strip()
                    if not expected:
                        continue
                    tc_m_raw = request.POST.get(f'q{i}_tcmarks_{tc_idx}', '1')
                    tc_m = int(tc_m_raw) if tc_m_raw.isdigit() else 1
                    CodingTestCase.objects.create(
                        question=question,
                        stdin=request.POST.get(f'q{i}_tcinput_{tc_idx}', ''),
                        expected_output=expected,
                        is_hidden=request.POST.get(f'q{i}_tchidden_{tc_idx}') == '1',
                        marks=tc_m,
                        order=tc_idx + 1,
                    )
                    total_tc_marks += tc_m
                # Sync question marks to sum of test-case marks
                if total_tc_marks > 0:
                    question.marks = total_tc_marks
                    question.save(update_fields=['marks'])

        if request.POST.get('action') == 'save_assign':
            return redirect('assign_students', exam_id=exam.id)
        return redirect('add_questions', exam_id=exam.id)

    questions = exam.questions.all().prefetch_related('choices')
    return render(request, 'teacher/add_questions.html', {
        'exam': exam,
        'questions': questions,
    })


@login_required(login_url='login')
def assign_students(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, teacher=request.user)

    if request.method == 'POST':
        student_ids = request.POST.getlist('student_ids')
        students = User.objects.filter(id__in=student_ids)
        for student in students:
            StudentExam.objects.get_or_create(student=student, exam=exam)
        exam.is_published = True
        exam.save()
        return redirect('teacher_dashboard')

    all_students = User.objects.filter(userrole__role='student')
    assigned_ids = list(exam.student_exams.values_list('student_id', flat=True))
    return render(request, 'teacher/assign_students.html', {
        'exam': exam,
        'students': all_students,
        'assigned_ids': assigned_ids
    })


@login_required(login_url='login')
def exam_results(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, teacher=request.user)
    student_exams = exam.student_exams.all()
    return render(request, 'teacher/exam_results.html', {
        'exam': exam,
        'student_exams': student_exams
    })


@login_required(login_url='login')
def student_answer_review(request, student_exam_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id)
    if student_exam.exam.teacher != request.user:
        return redirect('teacher_dashboard')
    answers = student_exam.answers.all().select_related('question', 'selected_choice').prefetch_related('question__choices')
    return render(request, 'teacher/review_answers.html', {
        'student_exam': student_exam,
        'exam': student_exam.exam,
        'student': student_exam.student,
        'answers': answers
    })


# ==================== Profile ====================

@login_required(login_url='login')
def profile(request):
    user = request.user
    try:
        user_role = UserRole.objects.get(user=user)
        role = user_role.role
    except UserRole.DoesNotExist:
        role = 'unknown'

    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_info':
            first_name = request.POST.get('first_name', '').strip()
            last_name  = request.POST.get('last_name', '').strip()
            email      = request.POST.get('email', '').strip()
            if email and email != user.email:
                if User.objects.filter(email=email).exclude(pk=user.pk).exists():
                    error = 'That email is already in use.'
                else:
                    user.email = email
            user.first_name = first_name
            user.last_name  = last_name
            if not error:
                user.save()
                success = 'Profile updated successfully.'

        elif action == 'change_password':
            current  = request.POST.get('current_password', '')
            new_pw   = request.POST.get('new_password', '')
            confirm  = request.POST.get('confirm_password', '')
            if not user.check_password(current):
                error = 'Current password is incorrect.'
            elif len(new_pw) < 6:
                error = 'New password must be at least 6 characters.'
            elif new_pw != confirm:
                error = 'New passwords do not match.'
            else:
                user.set_password(new_pw)
                user.save()
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, user)
                success = 'Password changed successfully.'

    # Stats
    if role == 'teacher':
        exams = Exam.objects.filter(teacher=user)
        total_exams = exams.count()
        published   = exams.filter(is_published=True).count()
        total_students = StudentExam.objects.filter(exam__teacher=user).values('student').distinct().count()
        completed_exams = StudentExam.objects.filter(exam__teacher=user, is_submitted=True).count()
        stats = {
            'total_exams': total_exams,
            'published': published,
            'total_students': total_students,
            'completed_exams': completed_exams,
        }
    else:
        assigned   = StudentExam.objects.filter(student=user).select_related('exam')
        total_assigned  = assigned.count()
        completed_count = assigned.filter(is_submitted=True).count()
        passed_count    = sum(1 for se in assigned.filter(is_submitted=True) if se.has_passed())
        scores = [se.obtained_marks for se in assigned.filter(is_submitted=True) if se.obtained_marks is not None]
        avg_score = round(sum(scores) / len(scores), 1) if scores else None
        stats = {
            'total_assigned': total_assigned,
            'completed': completed_count,
            'passed': passed_count,
            'failed': completed_count - passed_count,
            'avg_score': avg_score,
        }

    return render(request, 'auth/profile.html', {
        'role': role,
        'stats': stats,
        'error': error,
        'success': success,
    })


# ==================== Student Views ====================

@login_required(login_url='login')
def student_dashboard(request):
    try:
        user_role = UserRole.objects.get(user=request.user)
        if user_role.role != 'student':
            return redirect('teacher_dashboard')
    except UserRole.DoesNotExist:
        return redirect('home')

    now = timezone.now()
    assigned_exams = StudentExam.objects.filter(student=request.user).select_related('exam')
    upcoming  = assigned_exams.filter(exam__start_time__gt=now, exam__is_published=True)
    available = assigned_exams.filter(exam__start_time__lte=now, exam__end_time__gte=now, is_submitted=False, exam__is_published=True)
    completed = assigned_exams.filter(is_submitted=True)

    return render(request, 'student/dashboard.html', {
        'upcoming': upcoming,
        'available': available,
        'completed': completed
    })


@login_required(login_url='login')
def start_exam(request, student_exam_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id, student=request.user)
    exam = student_exam.exam
    now = timezone.now()

    if now < exam.start_time:
        return render(request, 'student/exam_error.html', {'error': 'Exam has not started yet'})
    if now > exam.end_time:
        return render(request, 'student/exam_error.html', {'error': 'Exam time has ended'})
    if student_exam.is_submitted:
        return render(request, 'student/exam_error.html', {'error': 'You have already submitted this exam'})

    if not student_exam.exam_started_at:
        student_exam.exam_started_at = now
        student_exam.save()

    questions = exam.questions.all().prefetch_related('choices').order_by('order')
    for question in questions:
        Answer.objects.get_or_create(student_exam=student_exam, question=question)

    # Effective end time: min(exam end time, start + duration)
    effective_end = min(
        exam.end_time,
        student_exam.exam_started_at + timezone.timedelta(minutes=exam.duration_minutes)
    )

    questions_data = []
    for q in questions:
        q_data = {
            'id': q.id,
            'text': q.question_text,
            'type': q.question_type,
            'marks': q.marks,
            'choices': [{'id': c.id, 'text': c.choice_text} for c in q.choices.all()]
        }
        if q.question_type == 'coding':
            q_data['sample_cases'] = [
                {'stdin': tc.stdin, 'expected': tc.expected_output}
                for tc in q.test_cases.filter(is_hidden=False).order_by('order')
            ]
        questions_data.append(q_data)

    return render(request, 'student/exam.html', {
        'student_exam': student_exam,
        'exam': exam,
        'questions': questions,
        'questions_json': json.dumps(questions_data),
        'end_exam_time': effective_end,
        'duration_seconds': exam.duration_minutes * 60,
    })


@require_POST
@login_required(login_url='login')
def save_answer(request, student_exam_id, question_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id, student=request.user)
    question = get_object_or_404(Question, id=question_id, exam=student_exam.exam)

    if student_exam.is_submitted:
        return JsonResponse({'error': 'Exam already submitted'}, status=400)

    data = json.loads(request.body)
    answer, _ = Answer.objects.get_or_create(student_exam=student_exam, question=question)

    if question.question_type in ['mcq', 'true_false']:
        choice_id = data.get('choice_id')
        if choice_id:
            answer.selected_choice_id = choice_id
    else:
        answer.written_answer = data.get('written_answer', data.get('answer_text', ''))

    answer.save()
    return JsonResponse({'status': 'saved'})


@require_POST
@login_required(login_url='login')
def record_tab_switch(request, student_exam_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id, student=request.user)

    if student_exam.is_submitted:
        return JsonResponse({'status': 'already_submitted', 'tab_switch_count': student_exam.tab_switch_count})

    student_exam.tab_switch_count += 1
    student_exam.total_suspicion_warnings += 1
    student_exam.save()

    if student_exam.tab_switch_count >= 3:
        # Server-side auto-submit as safety net (JS will also call /submit/ but this ensures it happens)
        if not student_exam.is_submitted:
            exam = student_exam.exam
            total_marks = 0
            for answer in student_exam.answers.all():
                question = answer.question
                if question.question_type in ['mcq', 'true_false']:
                    if answer.selected_choice and answer.selected_choice.is_correct:
                        answer.is_correct = True
                        answer.marks_obtained = question.marks
                        total_marks += question.marks
                    else:
                        answer.is_correct = False
                        answer.marks_obtained = 0
                elif question.question_type == 'coding':
                    code = answer.written_answer or ''
                    m, graded = _grade_coding_answer(question, code)
                    if graded and m is not None:
                        answer.marks_obtained = m
                        answer.is_correct = (m == sum(tc.marks for tc in question.test_cases.all()))
                        total_marks += m
                    else:
                        answer.is_correct = None
                        answer.marks_obtained = None
                else:
                    answer.is_correct = None
                    answer.marks_obtained = None
                answer.save()
            student_exam.obtained_marks = total_marks
            student_exam.is_submitted = True
            student_exam.submitted_at = timezone.now()
            student_exam.save()
        return JsonResponse({'status': 'auto_submit', 'tab_switch_count': student_exam.tab_switch_count,
                             'redirect_url': reverse('exam_result', args=[student_exam.id])})

    return JsonResponse({'status': 'recorded', 'tab_switch_count': student_exam.tab_switch_count})


@require_POST
@login_required(login_url='login')
def submit_exam(request, student_exam_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id, student=request.user)

    if student_exam.is_submitted:
        return JsonResponse({'error': 'Exam already submitted'}, status=400)

    exam = student_exam.exam
    answers = student_exam.answers.all()
    total_marks = 0

    for answer in answers:
        question = answer.question
        if question.question_type in ['mcq', 'true_false']:
            if answer.selected_choice and answer.selected_choice.is_correct:
                answer.is_correct = True
                answer.marks_obtained = question.marks
                total_marks += question.marks
            else:
                answer.is_correct = False
                answer.marks_obtained = 0
        elif question.question_type == 'coding':
            code = answer.written_answer or ''
            m, graded = _grade_coding_answer(question, code)
            if graded and m is not None:
                answer.marks_obtained = m
                answer.is_correct = (m == sum(tc.marks for tc in question.test_cases.all()))
                total_marks += m
            else:
                answer.is_correct = None
                answer.marks_obtained = None  # teacher will review
        else:
            answer.is_correct = None
            answer.marks_obtained = None
        answer.save()

    student_exam.obtained_marks = total_marks
    student_exam.is_submitted = True
    student_exam.submitted_at = timezone.now()
    student_exam.save()

    return JsonResponse({
        'status': 'submitted',
        'marks': total_marks,
        'total': exam.total_marks,
        'redirect_url': reverse('exam_result', args=[student_exam.id])
    })


@login_required(login_url='login')
def exam_result(request, student_exam_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id, student=request.user)

    if not student_exam.is_submitted:
        return render(request, 'student/exam_error.html', {'error': 'Exam not submitted yet'})

    answers = student_exam.answers.all().select_related('question', 'selected_choice').prefetch_related('question__choices')
    return render(request, 'student/result.html', {
        'student_exam': student_exam,
        'exam': student_exam.exam,
        'answers': answers
    })


def get_time_remaining(request, student_exam_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id, student=request.user)
    exam = student_exam.exam
    now = timezone.now()
    time_remaining = exam.end_time - now
    seconds_remaining = max(0, int(time_remaining.total_seconds()))

    return JsonResponse({'seconds': seconds_remaining, 'total': exam.duration_minutes * 60})


# ==================== Proctoring Snapshot ====================

@require_POST
@login_required(login_url='login')
def save_snapshot(request, student_exam_id):
    student_exam = get_object_or_404(StudentExam, id=student_exam_id, student=request.user)
    # Accept snapshots even after submission (for safety), but not if exam doesn't belong to student
    try:
        data = json.loads(request.body)
        image_b64 = data.get('image', '').strip()
        snap_type = data.get('type', 'webcam')
        flag_reason = data.get('flag_reason', '')

        if not image_b64 or len(image_b64) < 100:
            return JsonResponse({'status': 'error', 'message': 'empty image'}, status=400)

        ProctorSnapshot.objects.create(
            student_exam=student_exam,
            snapshot_type=snap_type,
            image_data=image_b64,
            is_flagged=bool(flag_reason),
            flag_reason=flag_reason,
        )
        return JsonResponse({'status': 'saved'})
    except Exception:
        return JsonResponse({'status': 'error'}, status=400)


@login_required(login_url='login')
def student_snapshots(request, student_exam_id):
    # Only the exam's teacher can view snapshots
    student_exam = get_object_or_404(
        StudentExam, id=student_exam_id, exam__teacher=request.user
    )
    snapshots = student_exam.snapshots.all().order_by('captured_at')
    total = snapshots.count()
    flagged = snapshots.filter(is_flagged=True).count()
    return render(request, 'teacher/snapshots.html', {
        'student_exam': student_exam,
        'snapshots': snapshots,
        'total': total,
        'flagged': flagged,
    })
